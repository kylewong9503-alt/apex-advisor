.PHONY: help install dev test format lint clean docker-up docker-down migrate

help:  ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies
	pip install -r requirements.txt

dev:  ## Start development server
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:  ## Run tests
	pytest -v

format:  ## Format code with black and isort
	black app/ tests/
	isort app/ tests/

lint:  ## Run linting with flake8
	flake8 app/ tests/

clean:  ## Clean up cache files
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +

docker-up:  ## Start Docker services
	docker-compose up -d

docker-down:  ## Stop Docker services
	docker-compose down

docker-logs:  ## View Docker logs
	docker-compose logs -f

migrate:  ## Run database migrations
	alembic upgrade head

migrate-create:  ## Create new migration (usage: make migrate-create MSG="description")
	alembic revision --autogenerate -m "$(MSG)"

reset-db:  ## Reset database (WARNING: destroys all data)
	docker-compose down -v
	docker-compose up -d db
	sleep 5
	alembic upgrade head