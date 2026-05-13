# Dockerfile — AgriMesh V4.0 FastAPI + Ollama Bridge
FROM python:3.13-slim

WORKDIR /app

# System deps for PostgreSQL + audio (optional)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home --shell /bin/sh agrimesh \
    && chown -R agrimesh:agrimesh /app
USER agrimesh

# Expose FastAPI + MCP ports
EXPOSE 8000 9001 9002 9003 9004

CMD ["sh", "scripts/startup.sh"]
