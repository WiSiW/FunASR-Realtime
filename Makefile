PYTHON ?= .venv/bin/python
PIP ?= $(PYTHON) -m pip

.PHONY: install install-backend install-frontend dev-backend dev-frontend test lint build docker-build docker-up docker-down docker-logs docker-preload clean

install: install-backend install-frontend

install-backend:
	$(PIP) install -e ".[dev,cli]"

install-frontend:
	npm --prefix frontend install

dev-backend:
	$(PYTHON) -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend:
	npm --prefix frontend run dev

test:
	$(PYTHON) -m pytest
	npm --prefix frontend run typecheck

lint:
	$(PYTHON) -m ruff check backend

build:
	npm --prefix frontend run build

docker-build:
	DOCKER_BUILDKIT=1 docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f backend frontend

docker-preload:
	docker compose run --rm backend python -m backend.scripts.preload_models

clean:
	rm -rf frontend/dist frontend/node_modules/.vite backend/**/__pycache__
