.PHONY: help install install_test init_db run test clean

help:
	@echo "make install      - 安装依赖"
	@echo "make init_db      - 初始化数据库"
	@echo "make run          - 启动服务"
	@echo "make clean        - 清理临时文件"

install:
	uv sync

init_db:
	uv run -m app._init_db

run:
	uv run -m app.main

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".venv" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "logs" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".coverage" -delete 2>/dev/null || true
