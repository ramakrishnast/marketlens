# MarketLens: everything runs in Docker. `make run`, then open http://localhost:8000
COMPOSE ?= docker compose
PORT ?= 8000

.PHONY: run demo test stop logs restart finbert fixtures shell clean dev-backend dev-frontend test-local

.env:
	@cp .env.example .env && echo "Created .env from .env.example (add your keys there)"

run: .env ## Build and start the app
	$(COMPOSE) up -d --build
	@echo ""
	@echo "MarketLens is starting at http://localhost:$(PORT)  (logs: make logs)"

demo: run ## Load the sample watchlist and pre-populate every page
	$(COMPOSE) exec app python -m scripts.demo
	@echo "Demo loaded: open http://localhost:$(PORT)"

test: .env ## Run the test-suite inside the container (sample data, no network, no keys)
	$(COMPOSE) build
	$(COMPOSE) run --rm --no-deps -e DATA_MODE=mock -e OPENAI_API_KEY= -e MARKETLENS_TESTING=1 -e DATA_DIR=/tmp/mltest app python -m pytest -q tests

stop:
	$(COMPOSE) down

restart: .env
	$(COMPOSE) up -d --build --force-recreate

logs:
	$(COMPOSE) logs -f --tail=200 app

finbert: .env ## Rebuild with local FinBERT sentiment (~2 GB download)
	INSTALL_FINBERT=1 $(COMPOSE) up -d --build
	@echo "FinBERT installed; the model downloads on first use and is cached in ./data/hf"

fixtures: run ## Re-capture bundled sample data from the live sources (needs SEC_USER_AGENT in .env)
	$(COMPOSE) exec app python -m scripts.capture_fixtures

shell:
	$(COMPOSE) exec app bash

clean:
	$(COMPOSE) down --rmi local

# ---- optional: native development (Python 3.12+ and Node 20+) ----
dev-backend:
	cd backend && uvicorn app.main:app --reload --port 8000
dev-frontend:
	cd frontend && npm install && npm run dev
test-local:
	cd backend && DATA_MODE=mock OPENAI_API_KEY= MARKETLENS_TESTING=1 DATA_DIR=/tmp/mltest python -m pytest -q tests
