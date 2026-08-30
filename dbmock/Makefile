.PHONY: help init-db generate smoke validate clean

help:
	@echo "make init-db      - 重建Doris业务数据库并创建表"
	@echo "make generate     - 生成数据"
	@echo "make smoke        - 生成七天小数据集"
	@echo "make validate     - 校验现有数据"
	@echo "make clean        - 清理临时文件"

init-db:
	uv run scripts/init_db.py

generate:
	uv run main.py

smoke:
	uv run main.py --smoke

validate:
	uv run main.py --validate-only

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
