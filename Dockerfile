# Build context: translator-develop/
# docker build -t translator-develop .
FROM python:3.11-slim-bookworm

RUN groupadd --system --gid 10001 nonroot \
 && useradd --system --gid 10001 --uid 10001 --create-home nonroot

WORKDIR /app

# Copy translator source
COPY pyproject.toml .
COPY app ./app

# Install translator dependencies
RUN pip install --no-cache-dir \
        fastapi==0.115.2 \
        "httpx>=0.27.0" \
        "pydantic>=2.12.5" \
        "pydantic-settings>=2.0.0" \
        uvicorn==0.13.4

RUN mkdir -p /data && chown 10001:10001 /data

USER nonroot

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
