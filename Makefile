.PHONY: bootstrap lint format-check typecheck imports test check lab-certs lab-up
bootstrap:
	uv sync --locked
lint:
	uv run ruff check .
format-check:
	uv run ruff format --check .
typecheck:
	uv run pyright
imports:
	uv run lint-imports
test:
	uv run pytest
check: lint format-check typecheck imports test
lab-certs:
	uv run crosstalk lab certs
lab-up:
	docker compose -f deploy/compose/lab.yml --profile mock up -d --wait
	uv run crosstalk lab provenance
