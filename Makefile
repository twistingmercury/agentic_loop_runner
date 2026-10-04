.PHONY: help test unit e2e analyze build install uninstall

.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\n\033[1mAvailable targets:\033[0m\n"} /^[a-zA-Z0-9_-]+:.*##/ { printf "  %-16s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""


test: analyze unit e2e ## Run analyze, then the unit tests, then the e2e tests

unit: ## Run the unit tests only
	uv run pytest tests/unit

e2e: ## Run the end-to-end tests only
	uv run pytest tests/e2e


analyze: ## Runs formatting and linting
	uv run ruff format
	uv run ruff check --fix

build: ## Builds the wheel
	build/build.sh

install: ## Install the locally build wheel
	uv tool install dist/alr-*.whl --force

uninstall: ## Uninstalls alr
	uv tool uninstall alr
