.PHONY: help init_db generate clean

help:
	@echo "Available targets:"
	@echo "  help      Show this help message"
	@echo "  init_db   Initialize database schema"
	@echo "  generate  Generate mock warehouse data"
	@echo "  clean     Remove local project environment files"

init_db:
	uv run core/init_db.py

generate:
	uv run -m core.generators.main

clean:
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
