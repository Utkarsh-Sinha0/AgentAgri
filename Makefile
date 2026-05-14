# AgriMesh V4.0 — Makefile
# One command to rule them all: `make demo`

.PHONY: help install setup seed load-wiki test eval run-bot run-api run-mcp-weather run-mcp-mandi demo demo-check pull-model check-vram clean lint migrate migrate-down migrate-revision pwa-build

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install Python dependencies
	pip install -r requirements.txt

setup: ## Initialize database and seed data
	python scripts/seed_data.py

seed: setup ## Alias for setup

load-wiki: ## Load wiki articles into database
	python scripts/seed_data.py --wiki-only

test: ## Run all 32 tests with coverage
	python -m pytest tests/ -v --cov=app --cov-report=term-missing

test-e4b: ## Run grammar + verifier tests
	python -m pytest tests/test_e4b_grammar.py -v

test-retrieval: ## Run retrieval pipeline tests
	python -m pytest tests/test_retrieval.py -v

test-agent: ## Run agent E2E tests
	python -m pytest tests/test_agent_e2e.py -v

eval: ## Run evaluation harness on 15 golden queries
	python -m app.eval

lint: ## Run Ruff linter
	ruff check app/ tests/ scripts/

format: ## Auto-format with Ruff
	ruff format app/ tests/ scripts/

migrate: ## Apply Alembic migrations
	alembic upgrade head

migrate-down: ## Roll back one Alembic migration
	alembic downgrade -1

migrate-revision: ## Generate Alembic migration, e.g. make migrate-revision MSG="add table"
	alembic revision --autogenerate -m "$(MSG)"

run-bot: ## Start Telegram bot only
	python -m app.bot.telegram_bot

run-api: ## Start FastAPI server (PWA + dashboard)
	python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

demo-check: ## Verify demo seed, memory palace, and local Ollama status
	python scripts/demo_readiness_check.py

run-mcp-weather: ## Start MCP weather server
	python -m app.mcp_servers.weather_server

run-mcp-mandi: ## Start MCP mandi server
	python -m app.mcp_servers.mandi_server

run-mcp-scheme: ## Start MCP scheme server
	python -m app.mcp_servers.scheme_server

run-mcp-finance: ## Start MCP finance server
	python -m app.mcp_servers.finance_server

pwa-build: ## Build Preact PWA
	cd pwa && npm install && npm run build

demo: setup load-wiki ## Full demo: DB + seed + wiki + start API
	@echo "🌾 AgriMesh V4.0 ready!"
	@echo "  API:        http://localhost:8000"
	@echo "  Docs:       http://localhost:8000/docs"
	@echo "  Eval:       http://localhost:8000/api/eval/latest"
	@echo "  Health:     http://localhost:8000/health"
	@echo "  Degradation: http://localhost:8000/api/health/degradation"
	@echo "  Start bot:  make run-bot"
	@echo "  Telegram:   send /demo first"
	python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

pull-model: ## Pull Gemma 4 E4B + E2B models via Ollama
	ollama pull gemma4:e4b
	ollama pull gemma4:e2b

check-vram: ## Check GPU VRAM usage
	@nvidia-smi --query-gpu=memory.used,memory.total --format=csv 2>/dev/null || echo "nvidia-smi not available"

clean: ## Clean generated files
	rm -rf data/agrimesh.db data/test_agrimesh.db
	rm -rf __pycache__ app/__pycache__ app/*/__pycache__ tests/__pycache__
	rm -rf .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
