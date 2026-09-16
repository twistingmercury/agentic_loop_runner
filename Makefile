.PHONY: help test analyze

.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\n\033[1mAvailable targets:\033[0m\n"} /^[a-zA-Z0-9_-]+:.*##/ { printf "  %-16s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""


test: analyze ## Run pytest
	uv sync
	uv run ruff check
	uv run pytest


analyze: ## Runs formatting and linting
	uv run ruff format
	uv run ruff check --fix