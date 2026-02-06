.PHONY: help install install_test init_db run test

help:
	@echo "make install      - 安装依赖"
	@echo "make install_test - 安装测试依赖"
	@echo "make init_db      - 初始化数据库"
	@echo "make run          - 启动服务"
	@echo "make test         - 运行测试"

install:
	uv sync

install_test:
	uv sync --group test

init_db:
	uv run app/utils/_init_db.py

run:
	uv run -m app.main

test:
	uv run pytest
