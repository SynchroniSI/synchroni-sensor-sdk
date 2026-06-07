# Development helpers for the Synchroni sensor SDK.
# Prefer Poetry so tools match the project lockfile.

POETRY ?= poetry
RUFF   := $(POETRY) run ruff
MYPY   := $(POETRY) run mypy

# Ruff lint / format paths (legacy ``sensor/`` and ``examples/`` are excluded in pyproject).
SRC := synchroni_sensor_sdk v2_comparison tests

# Mypy targets (tests are intentionally omitted; mocks fail strict mode).
TYPECHECK_SRC := synchroni_sensor_sdk v2_comparison

.PHONY: help lint format format-check typecheck check fix

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

lint: ## Run Ruff linter (check only)
	$(RUFF) check $(SRC)

format: ## Format code with Ruff
	$(RUFF) format $(SRC)

format-check: ## Check formatting without writing (CI-friendly)
	$(RUFF) format --check $(SRC)

typecheck: ## Run mypy
	$(MYPY) $(TYPECHECK_SRC)

fix: ## Auto-fix Ruff issues, then format
	$(RUFF) check --fix $(SRC)
	$(RUFF) format $(SRC)

check: lint format-check typecheck ## Lint, format-check, and typecheck (no writes)
