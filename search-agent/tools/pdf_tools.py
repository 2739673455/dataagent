import os
import logging
try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated
from langchain_core.tools import tool
from api.monitor import monitor
from api.context import get_session_context

def _resolve_path(filename: str, session_dir: str) -> str:
    """
    辅助函数：解析文件路径，处理虚拟路径和会话上下文
    """
    # 1. 虚拟路径清洗
    virtual_prefixes = ["/workspace", "/mnt/data", "/home/user"]
    for prefix in virtual_prefixes:
        if filename.startswith(prefix):
            filename = filename[len(prefix):].lstrip("/\\")
            break

    if not session_dir:
        return os.path.abspath(filename)

    session_basename = os.path.basename(session_dir)

    # 2. 结合 Session Context
    if not os.path.isabs(filename):
        # 尝试2: 检查 filename 是否包含了 session_basename
        if f"{session_basename}/" in filename.replace("\\", "/") or (session_basename + "\\") in filename:
             return os.path.join(session_dir, os.path.basename(filename))
        # 尝试3: 检查是否以 output/ 开头 (处理 output/session_xxx 这种常见模式)
        elif filename.startswith("output/") or filename.startswith("output\\"):
             return os.path.join(session_dir, os.path.basename(filename))
        else:
             return os.path.join(session_dir, filename)
             
    else:
        # 尝试基于 CWD 解析
        cwd_abs_path = os.path.abspath(filename)
        try:
            is_in_session = os.path.commonpath([session_dir, cwd_abs_path]) == os.path.normpath(session_dir)
        except ValueError:
            is_in_session = False
            
        if is_in_session:
             # 检测嵌套
             if f"{session_basename}\\{session_basename}" in cwd_abs_path or f"{session_basename}/{session_basename}" in cwd_abs_path.replace("\\", "/"):
                 return os.path.join(session_dir, os.path.basename(filename))
             else:
                 return cwd_abs_path
                 
        elif os.path.isabs(filename) and not os.path.splitdrive(filename)[0]:
            # Unix 风格绝对路径 (无盘符) -> 视为相对于 session_dir
            return os.path.join(session_dir, filename.lstrip("/\\"))
            
        return cwd_abs_path

@tool
def convert_md_to_pdf(
    md_filename: Annotated[str, "要转换的Markdown文档路径（包含.md后缀）"],
    pdf_filename: Annotated[str, "输出的PDF文件路径（可选，默认与源文件同名）"] = None
) -> str:
    """
    读取已生成的Markdown文档（.md），并将其转换为PDF文件。
    """
    monitor.report_tool("Markdown转PDF工具")

    # 自动补充后缀
    if not md_filename.endswith('.md'):
        md_filename += '.md'
    
    session_dir = get_session_context()
    md_filename = _resolve_path(md_filename, session_dir)
    
    if not os.path.exists(md_filename):
        # 尝试等待文件生成 (处理并发时序问题)
        import time
        for _ in range(5):
            time.sleep(1)
            if os.path.exists(md_filename):
                break
        else:
            return f"错误：找不到源文件 '{md_filename}'。请确保文件已生成。"

    if not pdf_filename:
        pdf_filename = md_filename.replace('.md', '.pdf')
    elif not pdf_filename.endswith('.pdf'):
        pdf_filename += '.pdf'
        
    pdf_filename = _resolve_path(pdf_filename, session_dir)
    
    try:
        import markdown
        import win32com.client
        import pythoncom
    except ImportError:
        return "转换失败：缺少必要库。请确保安装了 'markdown' 和 'pywin32' (windows下)。"
        
    try:
        # 1. 读取Markdown内容
        with open(md_filename, 'r', encoding='utf-8') as f:
            text = f.read()
            
        # 2. 转换为HTML
        # 增加一些基本的样式以优化在Word中的显示效果
        html_body = markdown.markdown(text, extensions=['tables', 'fenced_code'])
        html_content = f"""
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: "Microsoft YaHei", "SimHei", sans-serif; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid black; padding: 8px; }}
            </style>
        </head>
        <body>
            {html_body}
        </body>
        </html>
        """
        
        # 3. 保存为临时HTML文件 (与源文件同目录)
        temp_html_path = md_filename.replace('.md', '.temp.html')
        temp_html_path = os.path.abspath(temp_html_path)
        
        with open(temp_html_path, "w", encoding="utf-8") as f:
            f.write(html_content)
            
        # 4. 调用 Word 进行转换 (HTML -> PDF)
        # 这种方式利用 Word 的渲染引擎，能完美处理中文字体和排版
        pythoncom.CoInitialize() # 确保在线程中可以使用 COM
        try:
            word = win32com.client.Dispatch('Word.Application')
            # word.Visible = False # 默认不可见
            
            doc = word.Documents.Open(temp_html_path)
            
            # wdFormatPDF = 17
            pdf_filename_abs = os.path.abspath(pdf_filename)
            doc.SaveAs(pdf_filename_abs, FileFormat=17)
            
            doc.Close(0) # wdDoNotSaveChanges = 0
            # 注意：频繁开启关闭 Word 可能会影响性能，但在 Tool 调用场景下通常可接受
            word.Quit()
        except Exception as com_error:
            # 尝试清理 Word 进程（如果需要），这里简单抛出
            raise com_error
        finally:
             # 清理临时文件
             if os.path.exists(temp_html_path):
                 os.remove(temp_html_path)
            
        if os.path.exists(pdf_filename):
            return f"成功将 '{md_filename}' 转换为 '{pdf_filename}' (使用 Word 引擎)。"
        else:
            return f"转换似乎完成了，但在路径下未找到生成的PDF文件: {pdf_filename}"
            
    except Exception as e:
        return f"转换PDF失败: {str(e)}"
