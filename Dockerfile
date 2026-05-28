# ── Stage 1: builder ──────────────────────────────────────────────────────────
# Install all Python dependencies into /install so we can copy only that
# into the lean runtime image (no pip, no build tools, no source extras).
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies for packages that need C extensions (asyncpg, bcrypt)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# Install into a dedicated prefix so we can copy it cleanly
RUN pip install --no-cache-dir --prefix=/install .


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user for security
RUN groupadd -r relay && useradd -r -g relay relay

WORKDIR /app

# Runtime OS libs only (libpq for asyncpg)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source (respects .dockerignore)
COPY app/     ./app/
COPY workers/ ./workers/

# Drop to non-root
USER relay

EXPOSE 8000

# Default: run the API gateway.
# Override the command in docker-compose to run workers instead.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
