.PHONY: auth

auth:
	cd auth && uv run -m app.main
