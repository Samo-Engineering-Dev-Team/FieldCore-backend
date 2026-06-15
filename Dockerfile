FROM python:3.12-slim

# Set workdir
WORKDIR /app

# Install system dependencies needed for building some Python packages
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       gcc \
       libpq-dev \
       curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for reproducible, lockfile-pinned installs (L1)
RUN pip install --no-cache-dir uv

# Copy dependency manifests first for layer caching; install from the lockfile
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app app
COPY scripts scripts

# Install the project itself (still frozen to the lockfile)
RUN uv sync --frozen --no-dev

EXPOSE 8000

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# uv run executes inside the project's locked virtualenv
CMD uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'
