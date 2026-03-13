# First, build the application in the `/app` directory.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /app/translator

# Copy dependency metadata first for better caching
COPY translator/pyproject.toml translator/uv.lock ./

# Copy libs dependencies
COPY slice-manager/libs/messages /app/slice-manager/libs/messages

# Create the virtualenv + install deps (without your project yet)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Now copy the full workspace
COPY translator /app/translator

# Second sync: with all files present; installs the project into .venv
WORKDIR /app/translator
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Then, use a final image without uv
FROM python:3.13-slim-bookworm

# Setup a non-root user
RUN groupadd --system --gid 10001 nonroot \
 && useradd --system --gid 10001 --uid 10001 --create-home nonroot

# Copy the application from the builder
COPY --from=builder --chown=nonroot:nonroot /app /app

# The venv lives under the service dir:
ENV PATH="/app/translator/.venv/bin:$PATH"

USER nonroot
WORKDIR /app/translator

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
