.PHONY: install data profile policy test lint mock up down

install:  ## install with dev extras
	pip install -e ".[dev]"

data:     ## generate the synthetic dataset (deterministic)
	hao generate --n 5000 --seed 42

profile:  ## print dataset quality profile
	hao profile

policy:   ## validate the operating-model policy
	hao policy validate

test:
	pytest -q

lint:
	ruff check src tests

mock:     ## run the mock ticketing API on :8001
	hao mock serve --port 8001

up:       ## postgres + mock ticketing in docker
	docker compose up --build -d

down:
	docker compose down
