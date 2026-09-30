FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends tshark ethtool iproute2 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
HEALTHCHECK --interval=10s --timeout=3s CMD python -c "import socket; s=socket.create_connection(('127.0.0.1',9200),2); s.close()"
CMD ["crosstalk", "capture", "serve", "--root", "/artifacts", "--interface", "eth0"]
