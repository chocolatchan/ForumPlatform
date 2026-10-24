FROM python:3.14-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    iptables \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml and install Python packages
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy application source code, docs, scripts, and migrations
COPY src/ /app/src/
COPY docs/ /app/docs/
COPY migrations/ /app/migrations/
COPY scripts/ /app/scripts/
COPY frontend/ /app/frontend/

EXPOSE 8000 8001 8002 8003 8004 8005 8006

CMD ["uvicorn", "src.gateway.app:app", "--host", "0.0.0.0", "--port", "8000"]
