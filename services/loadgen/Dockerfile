FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-bookworm
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY experiments /app/experiments
ENV PATH="/app/.venv/bin:$PATH"
USER 10001:10001
CMD ["sleep", "infinity"]
