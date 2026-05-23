# =============================================================================
# Stage 1 — Build dependencies
# =============================================================================
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt --target=/install

# =============================================================================
# Stage 2 — Runtime
# =============================================================================
FROM python:3.12-slim

# System tools (curl for healthcheck, libpq for asyncpg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

# Copy installed dependencies from builder
COPY --from=builder /install /usr/local/lib/python3.12/site-packages

# Copy application code
COPY . .

# Writable directories
RUN mkdir -p /app/data /app/uploads /app/outputs && \
    chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
