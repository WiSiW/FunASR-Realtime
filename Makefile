PYTHON ?= .venv/bin/python
PIP ?= $(PYTHON) -m pip

.PHONY: install install-backend install-frontend dev-backend dev-backend-watch dev-backend-reload-models model-daemon model-daemon-reload model-daemon-stop dev-frontend check-extension test lint build docker-build docker-up docker-down docker-reload-models docker-logs docker-preload clean

install: install-backend install-frontend

install-backend:
	$(PIP) install -e ".[dev,cli]"

install-frontend:
	npm --prefix frontend install

dev-backend:
	$(PYTHON) -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

dev-backend-watch:
	$(PYTHON) -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

dev-backend-reload-models:
	FUNASR_RELOAD_MODELS=true $(PYTHON) -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

model-daemon:
	$(PYTHON) -m backend.scripts.model_daemon

model-daemon-reload:
	$(PYTHON) -m backend.scripts.model_daemon --reload

model-daemon-stop:
	$(PYTHON) -m backend.scripts.model_daemon --stop

dev-frontend:
	npm --prefix frontend run dev

check-extension:
	npm --prefix chrome-extension run check

test:
	$(PYTHON) -m pytest
	npm --prefix frontend run typecheck
	npm --prefix chrome-extension run check

lint:
	$(PYTHON) -m ruff check backend

build:
	npm --prefix frontend run build

docker-build:
	DOCKER_BUILDKIT=1 docker compose build

docker-up:
	docker compose up -d

docker-reload-models:
	docker compose restart model-daemon

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f model-daemon backend frontend

docker-preload:
	docker compose run --rm --no-deps backend python -m backend.scripts.preload_models

clean:
	rm -rf frontend/dist frontend/node_modules/.vite backend/**/__pycache__
