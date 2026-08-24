.PHONY: help run worker beat clean

help:
	@echo "make run         - 启动后端服务"
	@echo "make worker      - 启动 Celery Worker"
	@echo "make beat        - 启动 Celery Beat"
	@echo "make clean       - 清理临时文件"

run:
	uv run main.py

worker:
	uv run celery -A app.shared.tasks.celery_app:celery_app worker -l INFO

beat:
	uv run celery -A app.shared.tasks.celery_app:celery_app beat -l INFO

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".venv" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "logs" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".coverage" -delete 2>/dev/null || true
	find . -type d -name "node_modules" -exec rm -rf {} + 2>/dev/null || true
