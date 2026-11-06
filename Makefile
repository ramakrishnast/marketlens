# MarketLens: everything runs in Docker (app + PostgreSQL). `make run`, then open http://localhost:$(PORT)
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

.PHONY: run demo test test-sqlite stop logs restart deploy update ps url backup restore psql migrate-sqlite destroy-db finbert fixtures shell clean dev-backend dev-frontend test-local

.env:
	@cp .env.example .env
	@pw=$$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'); \
		sed "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$$pw/" .env > .env.tmp && mv .env.tmp .env
	@echo "Created .env from .env.example with a random database password (add your keys there)"

run: .env ## Build and start the app + PostgreSQL
	$(COMPOSE) up -d --build
	@echo ""
	@echo "MarketLens is starting at http://$(URL_HOST):$(PORT)  (bound to $(HOST_BIND):$(PORT); logs: make logs)"

demo: run ## Load the sample watchlist and pre-populate every page
	$(COMPOSE) exec app python -m scripts.demo
	@echo "Demo loaded: open http://$(URL_HOST):$(PORT)"

test: .env ## Run the test-suite inside the container on SQLite and on Postgres (sample data, no keys)
	$(COMPOSE) build app
	$(COMPOSE) run --rm --no-deps -e DATA_MODE=mock -e OPENAI_API_KEY= -e MARKETLENS_TESTING=1 -e DATA_DIR=/tmp/mltest -e DATABASE_URL= -e POSTGRES_HOST= app python -m pytest -q tests
	$(COMPOSE) run --rm -e DATA_MODE=mock -e OPENAI_API_KEY= -e MARKETLENS_TESTING=1 -e DATA_DIR=/tmp/mltest app sh -c 'TEST_DATABASE_URL="host=$$POSTGRES_HOST user=$$POSTGRES_USER password=$$POSTGRES_PASSWORD dbname=marketlens_test" python -m pytest -q tests'

test-sqlite: .env ## Same, SQLite only (no database container needed)
	$(COMPOSE) build app
	$(COMPOSE) run --rm --no-deps -e DATA_MODE=mock -e OPENAI_API_KEY= -e MARKETLENS_TESTING=1 -e DATA_DIR=/tmp/mltest -e DATABASE_URL= -e POSTGRES_HOST= app python -m pytest -q tests

stop: ## Stop and remove the containers (the Postgres volume with your data is kept)
	$(COMPOSE) down

restart: .env ## Rebuild and recreate (use after editing .env, PORT, HOST_BIND)
	$(COMPOSE) up -d --build --force-recreate
	@echo "MarketLens restarted at http://$(URL_HOST):$(PORT)"

deploy: .env ## Pull the latest code, back up the database, rebuild and restart
	git pull --ff-only
	-@$(MAKE) --no-print-directory backup
	$(COMPOSE) up -d --build --force-recreate
	@echo "Deployed: http://$(URL_HOST):$(PORT)"

update: deploy

ps: ## Container status and health (app + postgres)
	$(COMPOSE) ps

url:
	@echo "http://$(URL_HOST):$(PORT)"

backup: ## Back up the database to ./data/backups/ (Postgres dump; SQLite copy if you still use the file)
	@mkdir -p data/backups
	@if $(COMPOSE) ps --status running --services 2>/dev/null | grep -qx postgres; then \
		f=data/backups/marketlens-$$(date +%Y%m%d-%H%M%S).dump; \
		$(COMPOSE) exec -T postgres sh -c 'pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -Fc' > $$f && echo "Postgres backup written to $$f"; \
	elif [ -f data/marketlens.db ]; then \
		cp data/marketlens.db data/backups/marketlens-$$(date +%Y%m%d-%H%M%S).db && echo "SQLite backup written to data/backups/"; \
	else echo "nothing to back up (is the stack running? make run)"; fi

restore: ## Restore a dump: make restore FILE=data/backups/marketlens-YYYYMMDD-HHMMSS.dump (replaces current data)
	@test -n "$(FILE)" -a -f "$(FILE)" || { echo "usage: make restore FILE=data/backups/<file>.dump"; exit 1; }
	$(COMPOSE) exec -T postgres sh -c 'pg_restore -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" --clean --if-exists --no-owner' < $(FILE)
	@echo "Restored from $(FILE)"

psql: ## Open a psql shell on the database
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

migrate-sqlite: ## Copy an old ./data/marketlens.db (from before Postgres) into Postgres (safe to re-run)
	$(COMPOSE) exec app python -m scripts.migrate_sqlite_to_postgres /app/data/marketlens.db

destroy-db: ## DELETE the Postgres volume and ALL collected data: make destroy-db CONFIRM=yes
	@test "$(CONFIRM)" = "yes" || { echo "This permanently deletes all collected data. Re-run with CONFIRM=yes"; exit 1; }
	$(COMPOSE) down -v

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

# ---- optional: native development (Python 3.12+ and Node 20+; uses SQLite unless DATABASE_URL is set) ----
dev-backend:
	cd backend && uvicorn app.main:app --reload --port $(PORT)
dev-frontend:
	cd frontend && npm install && npm run dev
test-local:
	cd backend && DATA_MODE=mock OPENAI_API_KEY= MARKETLENS_TESTING=1 DATA_DIR=/tmp/mltest python -m pytest -q tests
