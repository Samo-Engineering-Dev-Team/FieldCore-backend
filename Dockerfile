FROM python:3.12-slim

# Set workdir
WORKDIR /app

# Install system dependencies needed for building some Python packages
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       gcc \
       libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir "uv==0.9.29"

# Copy lockfile first for reproducible dependency installs
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app app
COPY scripts scripts

EXPOSE 8000

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV FORWARDED_ALLOW_IPS=*

CMD ["sh", "-c", "exec gunicorn -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:${PORT:-8000} app.main:app"]
