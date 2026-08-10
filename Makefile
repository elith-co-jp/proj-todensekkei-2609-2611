.PHONY: install dev build serve test docker-up docker-down docker-logs clean

install:
	pip install -r requirements.txt
	cd frontend && npm install

dev:
	uvicorn main:app --reload --port 8000

ui:
	cd frontend && npm run dev

build:
	cd frontend && npm run build

serve: build
	uvicorn main:app --port 8000

test:
	python3 -m pytest

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

clean:
	rm -rf __pycache__ */__pycache__ .pytest_cache frontend/dist
