# AgentAgri Setup

## Prerequisites

- Docker Desktop with Compose v2
- Ollama running on the host at `http://localhost:11434`
- Gemma-compatible local models pulled in Ollama, for example:

```powershell
ollama pull gemma4:e4b
ollama pull gemma4:e2b
```

For local development without Docker, use Python 3.11+ and Node.js 20+.

## 3-Command Docker Compose Quickstart

```powershell
copy .env.example .env
docker-compose build
docker-compose up
```

Then open:

- API and PWA: `http://localhost:8000`
- Health: `http://localhost:8000/health`
- Swagger docs in development: `http://localhost:8000/docs`

The Compose stack starts PostgreSQL, Redis, the FastAPI app, and all four MCP services. Every service has a healthcheck.

## Local Development

```powershell
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe scripts\seed_data.py
venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Build the PWA:

```powershell
cd pwa
npm install
npm run build
```

Note: `react-router-dom` registry installation was blocked in the current sandbox, so the PWA uses a local v6-style router compatibility shim at `pwa/src/vendor/react-router-dom.jsx`.

## Environment Variables

| Variable | Required | Default | Notes |
|---|---:|---|---|
| `APP_ENV` | yes | `development` | `development`, `test`, `demo`, or `production` |
| `DATABASE_URL` | yes | SQLite dev DB | Use `postgresql+asyncpg://...` for Docker/production |
| `OLLAMA_HOST` | yes | `http://localhost:11434` | Host URL for Ollama |
| `OLLAMA_MODEL` | yes | `gemma4:e4b` | Primary model shown in the UI toggle |
| `OLLAMA_FALLBACK_MODEL` | yes | `gemma4:e2b` | Fallback model shown in the UI toggle |
| `AGRIMESH_REQUIRE_API_KEY` | yes | `true` | Compose sets `false` for dev quickstart |
| `AGRIMESH_API_KEY` | production | empty | Required in production, minimum 32 chars |
| `ALLOWED_ORIGINS` | production | localhost | Comma-separated CORS allowlist |
| `ALLOWED_HOSTS` | production | localhost/testserver | Comma-separated trusted hosts |
| `TELEGRAM_BOT_TOKEN` | production | empty | Required in production config validation |
| `MAX_REQUEST_BYTES` | no | `2000000` | Request body limit |
| `USE_GRAMMAR_DECODING` | no | `true` | Structured model output control |
| `EVAL_PUBLIC_TOKEN` | no | empty | Optional narrow token for eval endpoints |

## Verification

```powershell
venv\Scripts\python.exe -m ruff check app tests scripts
venv\Scripts\python.exe -m pytest tests -q
venv\Scripts\python.exe -m py_compile app\main.py app\config.py app\models.py app\models_memory.py
docker-compose config --quiet
cd pwa; npm run build
```

