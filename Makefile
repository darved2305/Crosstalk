.PHONY: bootstrap lint format-check typecheck imports test check lab-certs lab-up platform-config platform-up
bootstrap:
	uv sync --locked --all-extras
lint:
	uv run ruff check .
format-check:
	uv run ruff format --check .
typecheck:
	uv run pyright
imports:
	uv run lint-imports
test:
	uv run pytest --cov=crosstalk --cov-report=term --cov-fail-under=80 -W error
check: lint format-check typecheck imports test
lab-certs:
	uv run crosstalk lab certs
lab-up:
	docker compose -f deploy/compose/lab.yml --profile mock up -d --wait
	uv run crosstalk lab provenance
platform-config:
	docker compose -f deploy/compose/platform.yml --env-file deploy/compose/platform.env config --quiet
platform-up:
	docker compose -f deploy/compose/platform.yml --env-file deploy/compose/platform.env up -d --build --wait
