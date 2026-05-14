# AgentAgri Setup

## Prerequisites

### Required: Ollama + Gemma 4 Model (Local AI)

1. **Download Ollama** from [ollama.ai](https://ollama.ai)
2. **Pull Gemma 4 model** (choose one):

```powershell
# RECOMMENDED: Fast, 4GB, good quality
ollama pull gemma4:2b

# OR: High-quality, 8.9GB, slower
ollama pull gemma4:e4b

# OR: Balanced, 5GB
ollama pull gemma4:e2b
```

**Ollama will start automatically and listen on `http://localhost:11434`**

### Optional: Local Development (Without Docker)

For development without Docker, use:
- Python 3.11+
- Node.js 20+
- PostgreSQL (or SQLite default)

## 3-Command Docker Compose Quickstart

### Setup

```powershell
copy .env.example .env
docker-compose build
docker-compose up
```

### How Docker Auto-Detects Your Ollama Instance

When you run `docker-compose up`:

1. **Docker network connects to your local Ollama** (`http://localhost:11434`)
2. **FastAPI backend automatically discovers your Gemma 4 model**
3. **Telegram bot automatically uses the connected model**
4. **All services have healthchecks** to ensure proper startup

**No environment variable changes needed!** Docker uses the default `OLLAMA_HOST=http://localhost:11434`

### Access the System

- **Web Dashboard**: `http://localhost:8000` (7-page professional UI)
- **API Health Check**: `http://localhost:8000/health` (system status)
- **Swagger API Docs**: `http://localhost:8000/docs` (interactive API testing)

### What Gets Started

The Docker Compose stack includes:
- ✅ PostgreSQL database (persistent storage)
- ✅ Redis cache (optional, for performance)
- ✅ FastAPI backend (connects to your local Ollama)
- ✅ 4 MCP microservices (weather, market prices, schemes, finance)
- ✅ React PWA frontend (responsive dashboard)
- ✅ Telegram bot service (connects to FastAPI)

All services have healthchecks and will restart on failure.

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

## Telegram Bot Setup (Optional but Recommended)

The Telegram bot automatically connects to AgentAgri and uses your Ollama instance for crop analysis.

### 1. Create Telegram Bot

1. Open Telegram app
2. Search for **@BotFather**
3. Send: `/newbot`
4. Follow prompts to name your bot
5. **Copy the bot token** (looks like `123456789:ABCDEFGhijklmnop`)

### 2. Add Token to AgentAgri

Edit your `.env` file:

```env
TELEGRAM_BOT_TOKEN=your_token_here
```

### 3. Restart Docker

```powershell
docker-compose down
docker-compose up
```

### 4. Start Using the Bot

In Telegram, find your bot and send:

```
/start          → Welcome & registration
/demo           → Load demo farm with sample data
📸 Photo        → Crop disease analysis (uses Gemma 4)
/prices         → Check mandi prices
/expense        → Log farming costs
/finance        → View P&L
/memory         → See field history
/help           → All commands
```

**The bot automatically uses your Gemma 4 model from Ollama for all AI analysis.**

---

## Verification

```powershell
venv\Scripts\python.exe -m ruff check app tests scripts
venv\Scripts\python.exe -m pytest tests -q
venv\Scripts\python.exe -m py_compile app\main.py app\config.py app\models.py app\models_memory.py
docker-compose config --quiet
cd pwa; npm run build
```

---

## System Architecture Diagram

```
Your Computer:
  Ollama (gemma4:2b)
    ↓ (localhost:11434)
  Docker Network
    ├─ FastAPI Backend (Python)
    │   ├─ Connects to Ollama
    │   ├─ Serves PWA frontend
    │   └─ Serves API endpoints
    ├─ PostgreSQL Database (data storage)
    ├─ Redis Cache (optional optimization)
    ├─ 4 MCP Microservices
    │   ├─ Weather Service
    │   ├─ Market Prices Service
    │   ├─ Schemes Service
    │   └─ Finance Service
    ├─ React Frontend (PWA)
    │   └─ 7-page dashboard
    └─ Telegram Bot Service
        └─ Connects to FastAPI backend

Farmer's Device:
  Telegram App ↔ Bot ↔ FastAPI ↔ Ollama (Gemma 4)
  Browser ↔ http://localhost:8000 ↔ FastAPI ↔ Ollama
```

**Everything runs locally. No cloud. No signup. No costs.**

