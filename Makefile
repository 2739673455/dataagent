.PHONY: help auth test_auth
help:
	@echo "make auth        - 启动 auth 服务"
	@echo "make test_auth   - 运行 auth 测试"

auth:
	cd auth && uv run -m app.main

test_auth:
	cd auth && uv run pytest
