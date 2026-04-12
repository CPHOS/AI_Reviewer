# ---- Build stage ----
FROM python:3.12-slim AS builder

# Install uv and git (git needed for cphos-qdb dependency)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency manifests first for layer caching
COPY pyproject.toml uv.lock* ./

# Install dependencies into a virtual env
RUN uv sync --no-dev --no-install-project

# Copy source code
COPY src/ src/

# Install the project itself
RUN uv sync --no-dev

# ---- Runtime stage ----
FROM python:3.12-slim

WORKDIR /app

# Copy the virtual env and source from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/pyproject.toml /app/pyproject.toml

# Ensure the venv is on PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Default output directory
RUN mkdir -p /app/output
VOLUME ["/app/output"]

ENTRYPOINT ["ai-reviewer"]
CMD ["server", "--auto-on"]
