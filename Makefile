.PHONY: help start run web init-db bootstrap-users clean

help:
	@echo "make start           - 启动前端和后端"
	@echo "make run             - 启动后端服务"
	@echo "make web             - 启动前端开发服务"
	@echo "make init-db         - 初始化数据库"
	@echo "make bootstrap-users - 初始化预定义用户和角色"
	@echo "make clean           - 清理临时文件"

start:
	$(MAKE) --no-print-directory -j2 run web

run:
	uv run main.py

web:
	npm --prefix web run dev

init-db:
	uv run scripts/init_db.py

bootstrap-users:
	uv run -m scripts.bootstrap_users

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".venv" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "logs" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".coverage" -delete 2>/dev/null || true
	find . -type d -name "node_modules" -exec rm -rf {} + 2>/dev/null || true
