# Build context: bolsa_IT/
# docker build -f translator-develop/Dockerfile .
FROM python:3.11-slim-bookworm

RUN groupadd --system --gid 10001 nonroot \
 && useradd --system --gid 10001 --uid 10001 --create-home nonroot

WORKDIR /app

# Copy the generated messages lib (local path dependency)
COPY slice-manager/libs/messages /app/libs/messages

# Copy translator source
COPY translator-develop/pyproject.toml .
COPY translator-develop/app ./app

# Install the libs dependency then the translator
RUN pip install --no-cache-dir /app/libs/messages && \
    pip install --no-cache-dir \
        fastapi==0.115.2 \
        "httpx>=0.27.0" \
        "pydantic>=2.12.5" \
        "pydantic-settings>=2.0.0" \
        uvicorn==0.13.4

USER nonroot

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
