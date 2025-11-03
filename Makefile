# Project variables (override via environment or CLI, e.g., `make docker-build VERSION=1.2.3`)
PROJECT_NAME ?= autovate
REGISTRY ?=
IMAGE_API ?= $(REGISTRY)$(PROJECT_NAME)-api
IMAGE_WORKER ?= $(REGISTRY)$(PROJECT_NAME)-worker
VERSION ?= latest

PYTHON ?= python3
PIP ?= pip3
VENV_DIR ?= .venv

COMPOSE_FILE ?= docker-compose.yml

# Load environment variables from .env if present
ENV_FILE ?= .env
ifneq (,$(wildcard $(ENV_FILE)))
include $(ENV_FILE)
export
endif

# ===== Local dev =====
.PHONY: help
help:
	@echo "Available targets:"
	@echo "  make setup               - Create venv and install dependencies"
	@echo "  make install             - Install Python dependencies into current env"
	@echo "  make run-api             - Run FastAPI app locally"
	@echo "  make run-worker          - Run Celery worker locally"
	@echo "  make fmt                 - Format code (ruff/black if available)"
	@echo "  make lint                - Lint code (ruff if available)"
	@echo "  make test                - Run tests (pytest if available)"
	@echo "  make docker-build        - Build API and Worker images"
	@echo "  make docker-up           - Start stack via docker-compose"
	@echo "  make docker-down         - Stop stack"
	@echo "  make docker-push         - Push images to registry"

.PHONY: setup
setup:
	$(PYTHON) -m venv $(VENV_DIR)
	. $(VENV_DIR)/bin/activate && $(PIP) install --upgrade pip
	. $(VENV_DIR)/bin/activate && $(PIP) install -r requirements.txt

.PHONY: install
install:
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

.PHONY: run-api
run-api:
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

.PHONY: run-worker
run-worker:
	celery -A app.celery_app:app worker --loglevel=INFO

.PHONY: fmt
fmt:
	@command -v ruff >/dev/null 2>&1 && ruff format . || echo "ruff not installed; skipping format"
	@command -v black >/dev/null 2>&1 && black . || echo "black not installed; skipping format"

.PHONY: lint
lint:
	@command -v ruff >/dev/null 2>&1 && ruff check . || echo "ruff not installed; skipping lint"

.PHONY: test
test:
	@command -v pytest >/dev/null 2>&1 && pytest -q || echo "pytest not installed; skipping tests"

# ===== Docker =====
.PHONY: docker-build
docker-build:
	docker build -t $(IMAGE_API):$(VERSION) -f Dockerfile.api .
	docker build -t $(IMAGE_WORKER):$(VERSION) -f Dockerfile.worker .

.PHONY: docker-push
docker-push:
	@if [ -z "$(REGISTRY)" ]; then echo "REGISTRY is empty; set REGISTRY to push (e.g., REGISTRY=ghcr.io/owner/)"; exit 1; fi
	docker push $(IMAGE_API):$(VERSION)
	docker push $(IMAGE_WORKER):$(VERSION)

.PHONY: docker-up
docker-up:
	docker compose -f $(COMPOSE_FILE) up -d --build

.PHONY: docker-down
docker-down:
	docker compose -f $(COMPOSE_FILE) down

# ===== Utilities =====
.PHONY: clean
clean:
	rm -rf $(VENV_DIR) __pycache__ **/__pycache__ .pytest_cache .ruff_cache
