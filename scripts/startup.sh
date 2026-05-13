#!/usr/bin/env bash
# AgriMesh V4.0 — One-Command Demo Startup
set -euo pipefail
echo "🌾 AgriMesh V4.0 — Starting..."
echo ""

# 1. Wait for PostgreSQL
echo "⏳ Waiting for PostgreSQL..."
until pg_isready -h postgres -U agrimesh -d agrimesh 2>/dev/null; do sleep 1; done
echo "✅ PostgreSQL ready"

# 2. Run migrations
echo "📦 Running database migrations..."
python -m alembic upgrade head

# 3. Seed data
echo "🌱 Seeding database..."
python scripts/seed_data.py

# 4. Warm Ollama (check connectivity)
echo "🤖 Checking Ollama..."
python -c "
from app.utils.ollama_client import get_ollama
import asyncio
async def warm():
    client = get_ollama()
    resp = await client.chat([{'role':'user','content':'ping'}], max_tokens=2)
    print(f'  Ollama: {resp[\"model_used\"]} ready ({resp[\"latency_ms\"]}ms)')
asyncio.run(warm())
" 2>/dev/null || echo "⚠️  Ollama not reachable — ensure it's running on host"

# 5. Start FastAPI
echo ""
echo "🚀 Starting AgriMesh API on http://0.0.0.0:8000"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
