.PHONY: install dev build serve test eval eval-quick docker-up docker-down docker-logs clean

install:
	pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt
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
	ruff check .
	cd frontend && npx tsc -b && npm test

# 合成回路・合成図面での評価（DD-0038）。基準値より悪くなれば失敗する
eval:
	python3 analysis/tools/eval_suite.py

eval-quick:
	python3 analysis/tools/eval_suite.py --quick

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

clean:
	rm -rf __pycache__ */__pycache__ .pytest_cache frontend/dist
