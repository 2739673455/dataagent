# 使用说明

1. 安装依赖 `uv sync`
2. 在 `core/init_db.py` 末尾修改数据库配置
3. 初始化数据库 `uv run core/init_db.py`
4. 生成数据 `uv run core/generate.py`