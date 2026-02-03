import os
import sys
import uuid
import asyncio
import uvicorn
import subprocess
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict
import shutil

# Add project root to sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

# Import agent runner and monitor
# 注意：Agent.simple_agents 导入时会初始化 main_agent，这可能需要几秒钟
from Agent.simple_agents import run_deep_agent
from api.monitor import monitor

app = FastAPI(title="DeepAgents API")

# 挂载输出目录，以便前端访问生成的静态文件
# 假设输出目录位于项目根目录下的 output
output_dir = os.path.join(project_root, "output")
if not os.path.exists(output_dir):
    os.makedirs(output_dir)
app.mount("/outputs", StaticFiles(directory=output_dir), name="outputs")

# 定义上传目录 updated
updated_dir = os.path.join(project_root, "updated")
if not os.path.exists(updated_dir):
    os.makedirs(updated_dir)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        # key: thread_id, value: list of websockets
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.loop = None

    async def connect(self, websocket: WebSocket, thread_id: str):
        await websocket.accept()
        if thread_id not in self.active_connections:
            self.active_connections[thread_id] = []
        self.active_connections[thread_id].append(websocket)
        
        # 捕获当前的事件循环，以便同步线程可以通过 run_coroutine_threadsafe 调用
        if self.loop is None:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

    def disconnect(self, websocket: WebSocket, thread_id: str):
        if thread_id in self.active_connections:
            if websocket in self.active_connections[thread_id]:
                self.active_connections[thread_id].remove(websocket)
            if not self.active_connections[thread_id]:
                del self.active_connections[thread_id]

    async def send_to_thread(self, message: dict, thread_id: str):
        """向特定 thread_id 的所有连接发送消息"""
        if thread_id in self.active_connections:
            for connection in self.active_connections[thread_id]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    print(f"Error sending to websocket: {e}")

    async def broadcast(self, message: dict):
        """向所有连接广播消息 (慎用)"""
        for thread_id, connections in self.active_connections.items():
            for connection in connections:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    print(f"Error sending to websocket: {e}")

manager = ConnectionManager()
# 将 manager 注入到 monitor 单例中
monitor.set_websocket_manager(manager)

class TaskRequest(BaseModel):
    query: str
    thread_id: str = None

@app.post("/api/task")
async def run_task(request: TaskRequest):
    """
    接收任务请求，并启动后台任务执行。
    实时进度将通过 WebSocket 推送。
    """
    thread_id = request.thread_id if request.thread_id else str(uuid.uuid4())
    
    # 获取当前事件循环
    loop = asyncio.get_running_loop()
    
    # 确保 manager 知道 loop
    if manager.loop is None:
        manager.loop = loop

    # 在后台线程中运行 Agent，避免阻塞主事件循环
    # run_deep_agent 是异步函数，使用 create_task 调度
    asyncio.create_task(run_deep_agent(request.query, thread_id))
    
    return {
        "status": "started", 
        "thread_id": thread_id, 
        "message": "Task started in background. Please connect to WebSocket for updates."
    }

@app.post("/api/upload")
async def upload_files(
    thread_id: str = Form(...),
    files: List[UploadFile] = File(...)
):
    """
    上传文件到 updated/session_{thread_id} 目录
    """
    # 增加日志校验
    print(f"Received upload request for thread_id: {thread_id}")
    print(f"Received files: {[f.filename for f in files]}")

    target_dir = os.path.join(updated_dir, f"session_{thread_id}")
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
    
    uploaded_file_names = []
    for file in files:
        file_path = os.path.join(target_dir, file.filename)
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)
        uploaded_file_names.append(file.filename)
        
    return {"status": "success", "files": uploaded_file_names}

@app.get("/api/download")
async def download_file(path: str):
    """
    下载指定绝对路径的文件
    """
    # 安全检查：确保路径在 output_dir 下
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(os.path.abspath(output_dir)):
        return {"error": "Access denied: Path must be within output directory"}
    
    if not os.path.exists(abs_path):
        return {"error": "File not found"}
        
    return FileResponse(abs_path, filename=os.path.basename(abs_path))

@app.get("/api/files")
async def list_files(path: str):
    """
    列出指定目录下的文件
    path: 绝对路径，必须在 output 目录下
    """
    # 安全检查：确保路径在 output_dir 下
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(os.path.abspath(output_dir)):
        return {"error": "Access denied: Path must be within output directory"}
    
    if not os.path.exists(abs_path):
        return {"error": "Path not found"}
        
    files = []
    try:
        # 使用 os.walk 递归遍历目录
        for root, dirs, filenames in os.walk(abs_path):
            for filename in filenames:
                file_path = os.path.join(root, filename)
                
                # 计算相对于 output_dir 的路径，用于生成 URL (保留，虽然下载用绝对路径)
                rel_path = os.path.relpath(file_path, output_dir)
                url_path = rel_path.replace("\\", "/")
                
                files.append({
                    "name": filename,
                    "type": "file",
                    "path": file_path,
                    "url": f"/outputs/{url_path}",
                    "size": os.path.getsize(file_path),
                    "mtime": os.path.getmtime(file_path)
                })
                
    except Exception as e:
        return {"error": str(e)}
        
    # 按时间倒序排列
    files.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    return {"files": files}

@app.websocket("/ws")
async def websocket_legacy(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json({"type": "error", "message": "Client outdated. Please refresh page."})
    await websocket.close(code=1000, reason="Client outdated")

@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    await manager.connect(websocket, thread_id)
    try:
        while True:
            # 保持连接活跃，并可以接收前端指令
            # 目前只作为简单的保活 echo
            data = await websocket.receive_text()
            await websocket.send_json({"type": "pong", "message": f"received: {data}"})
    except WebSocketDisconnect:
        manager.disconnect(websocket, thread_id)
    except Exception as e:
        print(f"WebSocket Error: {e}")
        manager.disconnect(websocket, thread_id)

if __name__ == "__main__":
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
