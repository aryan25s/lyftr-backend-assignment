PYTHON ?= python
UVICORN ?= uvicorn

.PHONY: run dev test lint docker-build docker-up docker-down

run:
	WEBHOOK_SECRET=$${WEBHOOK_SECRET:-changeme} DATABASE_URL=$${DATABASE_URL:-./app.db} LOG_LEVEL=$${LOG_LEVEL:-INFO} $(UVICORN) app.main:app --reload --host 0.0.0.0 --port 8000

dev: run

test:
	$(PYTHON) -m pytest -vv

docker-build:
	docker build -t lyftr-webhook-service .

docker-up:
	docker-compose up --build

docker-down:
	docker-compose down

