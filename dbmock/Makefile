.PHONY: help init_db generate clean

help:
	@echo "make init_db      - 初始化数据库"
	@echo "make generate     - 生成数据"
	@echo "make clean        - 清理临时文件"

init_db:
	uv run core/init_db.py

generate:
	uv run -m core.generators.main

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
