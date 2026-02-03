import os
try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated
from langchain_core.tools import tool
from api.monitor import monitor
from api.context import get_session_context

# Markdown生成工具
@tool
def generate_markdown(
    content: Annotated[str, "要写入Markdown文档的文本内容"],
    filename: Annotated[str, "Markdown文档的文件名（不包含扩展名或包含.md）"],
    path: Annotated[str, "文件保存的绝对路径"] = ""
):
    """根据提供的文本内容，生成对应的Markdown(.md)文件"""
    print(f"路径是{path}")
    monitor.report_tool("Markdown文档生成工具",{"写入的文本内容":content})
    if not filename.endswith('.md'):
        filename += '.md'
    
    # 获取上下文中的会话目录
    session_dir = get_session_context()

    # --- 路径清洗与重定向逻辑 ---
    # 1. 如果路径包含常见的虚拟前缀，强制剥离
    virtual_prefixes = ["/workspace", "/mnt/data", "/home/user"]
    
    # 清洗 filename
    for prefix in virtual_prefixes:
        if filename.startswith(prefix):
            filename = filename[len(prefix):].lstrip("/\\")
            break
            
    # 清洗 path
    if path:
        for prefix in virtual_prefixes:
            if path.startswith(prefix):
                path = path[len(prefix):].lstrip("/\\")
                break

    # 2. 构建最终路径
    if session_dir:
        # 尝试将 path 视为相对于 CWD 的路径，并转为绝对路径
        # 这涵盖了 Agent 传递 "output/session_xxx/file.md" 的情况
        if path:
             cwd_abs_path = os.path.abspath(os.path.join(path, filename))
        else:
             cwd_abs_path = os.path.abspath(filename)
        
        # 检查这个路径是否已经在 session_dir 内部
        # 注意：这里做简单的字符串包含检查，或者 commonpath 检查
        try:
            is_in_session = os.path.commonpath([session_dir, cwd_abs_path]) == os.path.normpath(session_dir)
        except ValueError:
            is_in_session = False

        if is_in_session:
             full_path = cwd_abs_path
             path = os.path.dirname(full_path)
        else:
             # 如果不在 session_dir 内，且 path 看起来像是一个 session_id 目录（Agent 有时会重复创建目录）
             # 例如 session_dir=.../session_123, path=session_123 -> full_path=.../session_123/session_123/file.md
             # 我们需要检测并避免这种嵌套
             
             session_basename = os.path.basename(session_dir)
             if path and (path == session_basename or path.endswith(f"/{session_basename}") or path.endswith(f"\\{session_basename}")):
                  # Agent 传递了 session_id 作为 path，但它实际上想要的是 session_dir 根目录
                  full_path = os.path.join(session_dir, filename)
                  path = session_dir
             else:
                 # 走之前的逻辑：强制拼接到 session_dir 下
                 if not path or path == ".":
                     full_path = os.path.join(session_dir, filename)
                     path = session_dir 
                 elif not os.path.isabs(path):
                     full_path = os.path.join(session_dir, path, filename)
                     path = os.path.join(session_dir, path)
                 elif os.path.isabs(path) and not os.path.splitdrive(path)[0]:
                      rel_path = path.lstrip("/\\")
                      full_path = os.path.join(session_dir, rel_path, filename)
                      path = os.path.join(session_dir, rel_path)
                 else:
                      full_path = os.path.join(path, filename)
    else:
        # 无上下文时的回退逻辑
        if not path or path == ".":
            path = "."
        full_path = os.path.join(path, filename)

    # 确保目录存在
    print(f"[MarkdownTool] Debug: path={path}, filename={filename}, full_path={full_path}")
    
    if path and path != "." and not os.path.exists(path):
        try:
            os.makedirs(path)
            print(f"[MarkdownTool] Created directory: {path}")
        except Exception as e:
            print(f"[MarkdownTool] Error creating directory: {e}")
            return f"创建目录失败: {str(e)}"
    
    try:
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"[MarkdownTool] Successfully wrote to: {full_path}")
        return f"Markdown文件 '{full_path}' 已成功生成并保存。"
    except Exception as e:
        print(f"[MarkdownTool] Error writing file: {e}")
        return f"生成Markdown文件失败: {str(e)}"
