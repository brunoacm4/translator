# Build context: this repository root (.. from infra/), selected via docker-compose.
# A repo-root .dockerignore keeps the context to pyproject.toml + app/.
FROM python:3.11-slim-bookworm

RUN groupadd --system --gid 10001 nonroot \
 && useradd --system --gid 10001 --uid 10001 --create-home nonroot

WORKDIR /app

# Copy translator source (relative to the repo-root build context)
COPY pyproject.toml .
COPY app ./app

# Install translator dependencies
RUN pip install --no-cache-dir \
        fastapi==0.115.2 \
        "httpx>=0.27.0" \
        "pydantic>=2.12.5" \
        "pydantic-settings>=2.0.0" \
        hypercorn==0.18.0

RUN mkdir -p /data && chown 10001:10001 /data

USER nonroot

# Hypercorn serves HTTP/1.1 and HTTP/2 cleartext (h2c) on the same port: the
# NEF calls N5 with HTTP/2 over plain TCP (docs/adr/0002).
CMD ["hypercorn", "app.main:app", "--bind", "0.0.0.0:8081"]
