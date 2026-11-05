# MarketLens: everything runs in Docker. `make run`, then open http://localhost:$(PORT)
#
# Port / bind address (first match wins):
#   1. make run PORT=9000 HOST_BIND=0.0.0.0     (command line)
#   2. PORT=9000 make run                       (shell environment)
#   3. PORT=9000 / HOST_BIND=0.0.0.0 in .env
#   4. defaults: PORT=8000, HOST_BIND=127.0.0.1 (only reachable from this machine)
COMPOSE ?= docker compose
ENV_VAL = $(shell [ -f .env ] && grep -E '^$(1)=' .env | tail -n1 | cut -d= -f2- | tr -d '"'"'"' ')
PORT ?= $(or $(call ENV_VAL,PORT),8000)
HOST_BIND ?= $(or $(call ENV_VAL,HOST_BIND),127.0.0.1)
export PORT HOST_BIND
URL_HOST = $(if $(filter 0.0.0.0,$(HOST_BIND)),localhost,$(HOST_BIND))

.PHONY: run demo test stop logs restart deploy update ps url backup finbert fixtures shell clean dev-backend dev-frontend test-local

.env:
	@cp .env.example .env && echo "Created .env from .env.example (add your keys there)"

run: .env ## Build and start the app
	$(COMPOSE) up -d --build
	@echo ""
	@echo "MarketLens is starting at http://$(URL_HOST):$(PORT)  (bound to $(HOST_BIND):$(PORT); logs: make logs)"

demo: run ## Load the sample watchlist and pre-populate every page
	$(COMPOSE) exec app python -m scripts.demo
	@echo "Demo loaded: open http://$(URL_HOST):$(PORT)"

test: .env ## Run the test-suite inside the container (sample data, no network, no keys)
	$(COMPOSE) build
	$(COMPOSE) run --rm --no-deps -e DATA_MODE=mock -e OPENAI_API_KEY= -e MARKETLENS_TESTING=1 -e DATA_DIR=/tmp/mltest app python -m pytest -q tests

stop: ## Stop and remove the container (data in ./data is kept)
	$(COMPOSE) down

restart: .env ## Rebuild and recreate (use after editing .env, PORT or HOST_BIND)
	$(COMPOSE) up -d --build --force-recreate
	@echo "MarketLens restarted at http://$(URL_HOST):$(PORT)"

deploy: .env ## Pull the latest code, back up the database, rebuild and restart
	git pull --ff-only
	-@$(MAKE) --no-print-directory backup
	$(COMPOSE) up -d --build --force-recreate
	@echo "Deployed: http://$(URL_HOST):$(PORT)"

update: deploy

ps: ## Container status and health
	$(COMPOSE) ps

url:
	@echo "http://$(URL_HOST):$(PORT)"

backup: ## Consistent copy of the SQLite database to ./data/backups/
	@mkdir -p data/backups
	@if [ -f data/marketlens.db ]; then \
		$(COMPOSE) exec -T app python -c "import sqlite3,time; s=sqlite3.connect('/app/data/marketlens.db'); d=sqlite3.connect('/app/data/backups/marketlens-'+time.strftime('%Y%m%d-%H%M%S')+'.db'); s.backup(d); print('backup written to data/backups/')" \
		|| cp data/marketlens.db data/backups/marketlens-$$(date +%Y%m%d-%H%M%S).db; \
	else echo "no database yet"; fi

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
	cd backend && uvicorn app.main:app --reload --port $(PORT)
dev-frontend:
	cd frontend && npm install && npm run dev
test-local:
	cd backend && DATA_MODE=mock OPENAI_API_KEY= MARKETLENS_TESTING=1 DATA_DIR=/tmp/mltest python -m pytest -q tests
