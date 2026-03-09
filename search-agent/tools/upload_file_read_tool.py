import os
import logging
from typing import Annotated, Optional
from langchain_core.tools import tool
from api.monitor import monitor
from api.context import get_session_context

# 尝试导入可选依赖，实现按需加载
try:
    import docx
except ImportError:
    docx = None

try:
    import pypdf
except ImportError:
    pypdf = None

try:
    import pandas as pd
except ImportError:
    pd = None


def _resolve_path(filename: str, session_dir: str) -> str:
    """
    辅助函数：解析文件路径，处理虚拟路径和会话上下文
    (逻辑复用自 pdf_tools.py，保持独立性)
    """
    # 1. 虚拟路径清洗
    virtual_prefixes = ["/workspace", "/mnt/data", "/home/user"]
    for prefix in virtual_prefixes:
        if filename.startswith(prefix):
            filename = filename[len(prefix):].lstrip("/\\")
            break

    # 特殊处理：如果路径以 updated/ 开头，说明是用户上传目录，不应拼接到 session_dir (output) 下
    # 而是应该相对于项目根目录解析
    # 无论是否是绝对路径，只要包含了 updated/session_... 模式，就尝试修正
    if "updated/" in filename.replace("\\", "/") or "updated\\" in filename:
        # 找到 updated/ 的起始位置
        normalized_filename = filename.replace("\\", "/")
        updated_index = normalized_filename.find("updated/")
        if updated_index != -1:
             # 截取从 updated/ 开始的部分
             relative_path = normalized_filename[updated_index:]
             # 假设当前工作目录就是项目根目录
             return os.path.abspath(relative_path)

    if not session_dir:
        return os.path.abspath(filename)

    session_basename = os.path.basename(session_dir)

    # 2. 结合 Session Context
    if not os.path.isabs(filename):

        # 尝试: 检查 filename 是否包含了 session_basename
        if f"{session_basename}/" in filename.replace("\\", "/") or (session_basename + "\\") in filename:
            return os.path.join(session_dir, os.path.basename(filename))
        # 尝试: 检查是否以 output/ 开头
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
            if f"{session_basename}\\{session_basename}" in cwd_abs_path or f"{session_basename}/{session_basename}" in cwd_abs_path.replace(
                    "\\", "/"):
                return os.path.join(session_dir, os.path.basename(filename))
            else:
                return cwd_abs_path
        elif os.path.isabs(filename) and not os.path.splitdrive(filename)[0]:
            return os.path.join(session_dir, filename.lstrip("/\\"))

        return cwd_abs_path


@tool
def read_file_content(
        filename: Annotated[str, "要读取的文件名或路径（支持 .md, .docx, .pdf, .xlsx, .xls）"],
        instruction: Annotated[str, "对提取内容的具体指令（例如：'提取摘要', '统计数据'）"] = "提取全部内容"
) -> str:
    """
    读取指定文件的内容。支持 Markdown(.md)、Word(.docx)、PDF(.pdf) 和 Excel(.xlsx/.xls)。
    对于 Excel 文件，会自动提供数据统计信息（head 和 describe）。
    """
    monitor.report_tool("文件内容读取工具", {"filename": filename, "instruction": instruction})

    session_dir = get_session_context()
    file_path = _resolve_path(filename, session_dir)

    if not os.path.exists(file_path):
        return f"错误：文件 '{filename}' 不存在 (解析路径: {file_path})。"

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == '.md' or ext == '.txt':
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()

        elif ext == '.docx':
            if docx is None:
                return "错误：未安装 'python-docx' 库，无法读取 Word 文件。"
            doc = docx.Document(file_path)
            full_text = []
            for para in doc.paragraphs:
                full_text.append(para.text)
            return '\n'.join(full_text)

        elif ext == '.pdf':
            if pypdf is None:
                return "错误：未安装 'pypdf' 库，无法读取 PDF 文件。"
            reader = pypdf.PdfReader(file_path)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text

        elif ext in ['.xlsx', '.xls']:
            if pd is None:
                return "错误：未安装 'pandas' 库，无法读取 Excel 文件。"

            try:
                df = pd.read_excel(file_path)
            except Exception as e:
                return f"读取 Excel 失败: {str(e)}"

            result = []
            result.append(f"文件: {filename}")
            result.append(f"行数: {len(df)}, 列数: {len(df.columns)}")
            result.append(f"列名: {', '.join(df.columns.astype(str))}")

            result.append("\n[前5行数据预览]:")
            result.append(df.head().to_string(index=False))

            result.append("\n[统计描述]:")
            result.append(df.describe().to_string())

            return "\n".join(result)

        else:
            # 尝试作为纯文本读取
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except UnicodeDecodeError:
                return f"错误：不支持的文件格式 '{ext}'，且无法作为文本读取。"

    except Exception as e:
        return f"读取文件出错: {str(e)}"