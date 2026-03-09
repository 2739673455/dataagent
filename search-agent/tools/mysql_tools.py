import os
import logging
from typing import List, Dict, Any, Union
from dotenv import load_dotenv
from api.monitor import monitor

# Try to import mysql.connector, but don't fail if missing
import mysql.connector
from mysql.connector import connect, Error


# Load environment variables
load_dotenv()

# LangChain / Agent imports
try:
    from typing import Annotated
except ImportError:
    from typing_extensions import Annotated
from langchain_core.tools import tool

# Configure logging
"""logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mysql_tools")"""

def get_db_config():
    """Get database configuration from environment variables."""
    config = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "collation": os.getenv("MYSQL_COLLATION", "utf8mb4_unicode_ci"),
        "autocommit": True,
        "sql_mode": os.getenv("MYSQL_SQL_MODE", "TRADITIONAL")
    }
    # Remove None values
    config = {k: v for k, v in config.items() if v is not None}
    
    return config

@tool
def list_sql_tables() -> Annotated[str, "数据库中可用表的列表"]:
    """列出配置的 MySQL 数据库中所有可用的表。"""
    monitor.report_tool("数据库表获取工具")
    config = get_db_config()
    try:
        if not all([config.get("user"), config.get("password"), config.get("database")]):
            return "Error: Database configuration missing (MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE)."

        with connect(**config) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SHOW TABLES")
                tables = cursor.fetchall()
                if not tables:
                    return "No tables found in the database."
                
                table_names = [table[0] for table in tables]
                return f"Available tables: {', '.join(table_names)}"
    except Error as e:
        # logger.error(f"Failed to list tables: {str(e)}")
        return f"Error listing tables: {str(e)}"

@tool
def get_table_data(
    table_name: Annotated[str, "要读取数据的表名"]
) -> Annotated[str, "表的前 100 行数据（CSV 格式）"]:


    """读取指定 MySQL 表的前 100 行数据。"""
    monitor.report_tool("数据库内容浏览工具", {"正在读取的表": table_name})
    config = get_db_config()
    try:
        if not all([config.get("user"), config.get("password"), config.get("database")]):
            return "Error: Database configuration missing."
            
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                # Basic check to ensure table_name is just a name
                # (Not a robust SQL injection prevention for table names, but helpful)
                safe_table_name = table_name.replace("`", "").replace(";", "").split()[0]
                
                cursor.execute(f"SELECT * FROM {safe_table_name} LIMIT 100")
                
                if cursor.description is None:
                    return f"Table {table_name} seems empty or invalid."
                    
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                
                result = [",".join(map(str, row)) for row in rows]
                header = ",".join(columns)
                
                return f"{header}\n" + "\n".join(result)
    except Error as e:
        # logger.error(f"Failed to read table {table_name}: {str(e)}")
        return f"Error reading table {table_name}: {str(e)}"

@tool
def execute_sql_query(
    query: Annotated[str, "要执行的 SQL 查询语句"]
) -> Annotated[str, "查询结果或成功消息"]:
    """在 MySQL 数据库上执行自定义 SQL 查询。
    用于复杂查询、联接或特定数据检索。
    """
    monitor.report_tool("数据库查询工具")
    if mysql is None:
        return "Error: 'mysql-connector-python' library is not installed."
        
    # Security Warning: This tool allows arbitrary SQL execution.
    config = get_db_config()
    try:
        if not all([config.get("user"), config.get("password"), config.get("database")]):
            return "Error: Database configuration missing."
        
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(query)
                
                if cursor.description is not None:
                    # It's a SELECT or similar returning data
                    columns = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()
                    
                    if not rows:
                        return f"Query executed successfully. No rows returned. Columns: {', '.join(columns)}"
                        
                    # Format as string
                    result_lines = []
                    result_lines.append(",".join(columns))
                    for row in rows:
                        result_lines.append(",".join(map(str, row)))
                        
                    return "\n".join(result_lines)
                else:
                    # It's an INSERT, UPDATE, DELETE, etc.
                    conn.commit()
                    return f"Query executed successfully. Rows affected: {cursor.rowcount}"
                    
    except Error as e:
        # logger.error(f"Failed to execute query: {str(e)}")
        return f"Error executing query: {str(e)}"

