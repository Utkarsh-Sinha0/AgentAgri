# Dockerfile — AgriMesh V4.0 FastAPI + PWA + Ollama Bridge
FROM node:22-alpine AS pwa-build

WORKDIR /app/pwa
COPY pwa/package*.json ./
RUN npm ci
COPY pwa/ ./
RUN npm run build

FROM python:3.13-slim

WORKDIR /app

# System deps for PostgreSQL + audio (optional)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev postgresql-client ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=pwa-build /app/pwa/dist ./pwa/dist
RUN useradd --create-home --shell /bin/sh agrimesh \
    && chown -R agrimesh:agrimesh /app
USER agrimesh

# Expose FastAPI + MCP ports
EXPOSE 8000 9001 9002 9003 9004 9005

CMD ["sh", "scripts/startup.sh"]
