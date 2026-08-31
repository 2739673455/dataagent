.PHONY: help start run web worker beat init-db bootstrap-admin clean

help:
	@echo "make start           - 启动前端、后端、Celery Worker 和 Celery Beat"
	@echo "make run             - 启动后端服务"
	@echo "make web             - 启动前端开发服务"
	@echo "make worker          - 启动 Celery Worker"
	@echo "make beat            - 启动 Celery Beat"
	@echo "make init-db         - 初始化数据库"
	@echo "make bootstrap-admin - 初始化管理员账号"
	@echo "make clean           - 清理临时文件"

start:
	$(MAKE) --no-print-directory -j4 run web worker beat

run:
	uv run main.py

web:
	npm --prefix web run dev

worker:
	uv run celery --app app.shared.tasks.celery_app:celery_app worker -l INFO

beat:
	uv run celery --app app.shared.tasks.celery_app:celery_app beat -l INFO

init-db:
	uv run scripts/init_db.py

bootstrap-admin:
	uv run -m scripts.bootstrap_admin

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".venv" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "logs" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".coverage" -delete 2>/dev/null || true
	find . -type d -name "node_modules" -exec rm -rf {} + 2>/dev/null || true
