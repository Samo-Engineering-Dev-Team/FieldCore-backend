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

# Copy only pyproject first for caching
COPY pyproject.toml pyproject.toml
COPY app app
COPY scripts scripts

# Upgrade pip and install project (uses pyproject)
RUN python -m pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir .

EXPOSE 8000

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'
