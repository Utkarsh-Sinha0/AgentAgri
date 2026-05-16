# AgentAgri Setup

This document is the operator's manual: install, configure, swap models, swap the Telegram bot identity, switch databases, run the test suite, and ship to production.

For per-package dependency justification see [DEPENDENCIES.md](DEPENDENCIES.md). For architecture see [ARCHITECTURE.md](ARCHITECTURE.md). For end-to-end action flows see [WORKFLOW.md](WORKFLOW.md).

---

## 1. Prerequisites

| Component | Minimum | Recommended | Notes |
|-----------|---------|-------------|-------|
| Python | 3.11 | 3.12 | async/await + Pydantic 2 compatibility |
| Ollama | 0.4.x | 0.5+ | hosts Gemma 4 locally; default at `http://localhost:11434` |
| RAM | 12 GB | 16 GB+ | gemma4:e4b needs ~10 GB free; e2b fits in ~5 GB |
| Disk | 15 GB | 30 GB+ | BGE-M3 (~2 GB) + reranker (~1.5 GB) + Gemma weights (~6 GB) + wiki corpus |
| GPU | not required | NVIDIA 8 GB+ | speeds Ollama and BGE; CPU fallback works |
| OS | Linux/macOS/Windows 10+ | — | tested on Windows 11 and Ubuntu 22.04 |

**External accounts:**
- Telegram bot token from [@BotFather](https://t.me/BotFather) (free, instant)
- Optional: OpenWeather, IMD, AgMarknet keys for live MCP data; mock data ships in `data/seed/`

---

## 2. Quick Start (Local Dev)

```bash
git clone <repo-url> AgentAgri
cd AgentAgri
python -m venv .venv
.venv\Scripts\activate                  # Windows
# source .venv/bin/activate              # Linux/macOS
pip install -r requirements.txt

cp .env.example .env                     # then edit .env
ollama pull gemma4:e4b
ollama pull gemma4:e2b                   # fallback, smaller

alembic upgrade head                     # creates tables in DATABASE_URL
python -m app.seed_wiki                  # one-time corpus ingest

# Start FastAPI dashboard + MCP servers + Telegram bot
uvicorn app.main:app --reload --port 8000
python -m app.mcp.weather_server         # in separate terminal
python -m app.mcp.mandi_server
python -m app.mcp.scheme_server
python -m app.mcp.finance_server
python -m app.bot.telegram_bot
```

Visit `http://localhost:8000` for the dashboard; message your bot on Telegram to talk to the agent.

---

## 3. Environment Reference

All settings live in `.env` (loaded by `app/config.py`). Production mode (`APP_ENV=production`) rejects placeholder API keys, SQLite URLs, demo session secrets, and `*` CORS.

### 3.1 LLM (Ollama / Gemma 4)

| Var | Default | Purpose |
|-----|---------|---------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama daemon URL |
| `OLLAMA_MODEL` | `gemma4:e4b` | Primary model — used for planning, templating, verification |
| `OLLAMA_FALLBACK_MODEL` | `gemma4:e2b` | Smaller model invoked if primary times out or OOMs |
| `OLLAMA_NUM_CTX` | `16384` | Context window. Drop to 8192 if you only have 8 GB RAM |
| `OLLAMA_NUM_BATCH` | `512` | Tokens per batch; raise on GPU, lower on CPU |
| `OLLAMA_KEEP_ALIVE` | `-1` | Keep model resident forever (avoids cold-load) |
| `USE_GRAMMAR_DECODING` | `1` | JSON grammar-constrained decoding for templates |

### 3.2 Database

| Var | Default | Purpose |
|-----|---------|---------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/agrimesh.db` | SQLAlchemy async DSN |

### 3.3 Telegram

| Var | Default | Purpose |
|-----|---------|---------|
| `TELEGRAM_BOT_TOKEN` | _empty_ | From [@BotFather](https://t.me/BotFather). Required to start the bot |

### 3.4 Retrieval

| Var | Default | Purpose |
|-----|---------|---------|
| `BGE_M3_MODEL` | `BAAI/bge-m3` | Dense embedder (in-process, sentence-transformers) |
| `BGE_RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | Cross-encoder reranker (FlagEmbedding) |
| `RETRIEVAL_TOP_K` | `10` | Candidates returned by dense search |
| `RERANKER_TOP_K` | `3` | Final passages after reranking |

### 3.5 MCP Tool Servers

| Var | Default | Purpose |
|-----|---------|---------|
| `MCP_WEATHER_PORT` | `9001` | OpenWeather + IMD wrapper |
| `MCP_MANDI_PORT` | `9002` | AgMarknet price feed |
| `MCP_SCHEME_PORT` | `9003` | Govt scheme matcher (PM-KISAN, KCC, etc.) |
| `MCP_FINANCE_PORT` | `9004` | Per-farmer cashflow + expense rollups |

### 3.6 Feature Flags

| Var | Default | Effect when `true` |
|-----|---------|--------------------|
| `USE_GEMMA_AUDIO` | `false` | Reserved for future Gemma audio variant |
| `ENABLE_VOICE_STT` | `false` | Activates Whisper STT in `app/services/voice.py` |
| `ENABLE_SMS` | `false` | Activates SMS fallback for farmers without Telegram |
| `ENABLE_BHOJPURI` | `false` | Enables Bhojpuri translation path |

### 3.7 Runtime & Security

| Var | Default | Notes |
|-----|---------|-------|
| `APP_ENV` | `development` | Set to `production` for strict validation |
| `ALLOWED_ORIGINS` | `localhost:8000,127.0.0.1:8000` | CORS allowlist — never `*` in production |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,0.0.0.0,testserver` | Host header check |
| `AGRIMESH_REQUIRE_API_KEY` | `false` | Require `X-API-Key` header on dashboard endpoints |
| `AGRIMESH_API_KEY` | _empty_ | Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `MAX_REQUEST_BYTES` | `2000000` | Hard cap on upload size |

---

## 4. How to Change the LLM Model

The agent talks to Ollama through `app/services/ollama_client.py`. Swapping models is **a single env change + a single pull**.

### 4.1 Switch to another Gemma size

```bash
ollama pull gemma4:e2b           # download
# in .env
OLLAMA_MODEL=gemma4:e2b
# restart the bot + dashboard
```

### 4.2 Swap to a non-Gemma model

```bash
ollama pull qwen2.5:7b           # or llama3.1:8b, mistral:7b, phi3:mini, etc.
# in .env
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_FALLBACK_MODEL=qwen2.5:3b
OLLAMA_NUM_CTX=8192              # adjust to model's max context
```

**What changes automatically:** prompt routing, ReAct planning, template generation, verifier loop — all flow through the single client and the same env keys.

**What you may need to tune:**

- `USE_GRAMMAR_DECODING` — non-Gemma models on Ollama vary in JSON grammar support. Set to `0` if you see template output corruption; the agent will fall back to free-form generation + post-hoc JSON repair.
- `OLLAMA_NUM_CTX` — match the model's published context window. Models that report 32k often degrade past 8k on CPU.
- `OLLAMA_NUM_BATCH` — drop to 128 on CPU-only hosts to avoid timeouts on long prompts.

### 4.3 Run two models on the same host

Ollama serves all pulled models from one daemon. The primary stays warm (`OLLAMA_KEEP_ALIVE=-1`); the fallback loads on demand. Reserve ~14 GB RAM if you want both resident simultaneously.

### 4.4 Run Ollama on a different machine

```bash
# in .env on the AgentAgri box
OLLAMA_HOST=http://192.168.1.50:11434
```

No code change required. The client uses plain HTTP and tolerates higher latency.

### 4.5 Switch to a hosted API (Claude, OpenAI, etc.)

This is a code change, not a config change. Replace `app/services/ollama_client.py` with an adapter that exposes the same `generate()` / `chat()` interface; everything upstream is provider-agnostic. The repo intentionally pins to a local provider so farmers' data stays on-device.

---

## 5. How to Change the Telegram Bot / API

### 5.1 Issue a new bot

1. Open Telegram, message [@BotFather](https://t.me/BotFather).
2. `/newbot` → name → username ending in `bot` (e.g. `agentagri_bot`).
3. Copy the token (`123456789:ABC...`).
4. In `.env` set `TELEGRAM_BOT_TOKEN=<token>` and restart `python -m app.bot.telegram_bot`.

### 5.2 Migrate to a new bot identity (rotate token)

1. Generate a new bot via BotFather.
2. Update `TELEGRAM_BOT_TOKEN` and restart the bot process.
3. Existing farmer records are keyed by **phone**, not Telegram chat ID — they re-bind on first `/start` from the new bot.
4. Revoke the old token from BotFather to prevent dual-listening.

### 5.3 Swap to webhook mode (production)

The default is long-polling (works behind NAT, zero infra). For multi-instance deployments or low-latency, switch to webhooks:

```python
# app/bot/telegram_bot.py — replace run_polling() with:
application.run_webhook(
    listen="0.0.0.0",
    port=8443,
    url_path=os.environ["TELEGRAM_BOT_TOKEN"],
    webhook_url=f"https://yourdomain.example/{os.environ['TELEGRAM_BOT_TOKEN']}",
)
```

Then expose port 8443 behind TLS (Cloudflare Tunnel, nginx + certbot, or a managed load balancer).

### 5.4 Replace Telegram with WhatsApp / SMS

The bot module is thin — the agent core is messaging-agnostic. To target WhatsApp:

1. Add `whatsapp-business-cloud-api` or Twilio SDK to `requirements.txt`.
2. Create `app/bot/whatsapp_bot.py` mirroring the handler surface in `app/bot/telegram_bot.py` (one handler per command, plus text/photo/voice).
3. Forward parsed messages to `AgentOrchestrator.process()` exactly as the Telegram bot does.
4. Map WhatsApp's button payloads to the same `route_to_thread` callbacks.

For SMS, flip `ENABLE_SMS=true` and wire your provider in `app/services/sms.py` (Twilio / Gupshup / Karix).

---

## 6. How to Change the Database

Default is SQLite for zero-config dev. Production should use Postgres.

```bash
# .env
DATABASE_URL=postgresql+asyncpg://agrimesh:secret@db.internal:5432/agrimesh
# then
alembic upgrade head
```

**Migration safety:** every schema change ships as an Alembic revision in `app/migrations/versions/`. Never edit existing revisions — add a new one. The reversibility rule applies (see [project memory](../README.md)).

To move data from SQLite → Postgres:
```bash
python scripts/sqlite_to_postgres.py --src ./data/agrimesh.db --dst $DATABASE_URL
```

---

## 7. Running the Test Suite

```bash
# Full suite (excluding the one pre-existing flaky e2e test)
pytest tests --ignore=tests/test_agent_e2e.py

# Single file
pytest tests/test_conversation_wiring.py -v

# Memory & evidence regression
pytest tests/test_memory_accuracy.py tests/test_evidence.py

# Eval harness (synthetic 200 cases)
python -m evals.run_eval --source synthetic --limit 200
python -m evals.run_eval --source farmer_qa --limit 20
```

Baseline at `origin/main` HEAD `9c7af01`: **216 passed in 152.24s.**

---

## 8. Why the requirements file is long

The dependency list (≈40 direct + transitives) reflects three things the project genuinely needs and won't shortcut:

1. **Local LLM stack** — `ollama`, `httpx`, `transformers`, `torch`, `sentence-transformers`, `FlagEmbedding`. Running inference on the farmer's device keeps PII local; the price is a real ML runtime, not a hosted-API SDK.
2. **Async-first web + tool layer** — `fastapi`, `uvicorn`, `pydantic`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `mcp`, `fastmcp`. Async is non-negotiable because the agent fans out to 4 MCP servers in parallel and streams Telegram replies.
3. **Farmer-language + media pipeline** — `python-telegram-bot`, `Pillow`, `opencv-python-headless`, `indic-nlp-library`, `langdetect`, `lxml`. Photos for disease ID, mixed-script Hindi/Hinglish input, and structured wiki ingestion each pull a different toolchain.

Plus the standard support cast: `argon2-cffi` (password hashing), `redis` (rate limiting), `pytest` + `pytest-asyncio` (216 tests). See [DEPENDENCIES.md](DEPENDENCIES.md) for per-package "why this and not that."

---

## 9. Production Checklist

- [ ] `APP_ENV=production` set
- [ ] `DATABASE_URL` points to Postgres, not SQLite
- [ ] `AGRIMESH_API_KEY` generated and required on dashboard endpoints
- [ ] `ALLOWED_ORIGINS` and `ALLOWED_HOSTS` enumerate real domains, no `*`
- [ ] `TELEGRAM_BOT_TOKEN` is a production bot, not a dev clone
- [ ] Ollama daemon kept warm with `OLLAMA_KEEP_ALIVE=-1`
- [ ] MCP servers run under a process manager (systemd, supervisord)
- [ ] Redis reachable for rate limiting (`REDIS_URL`)
- [ ] Backups configured for the Postgres `farmers` / `conversation_threads` / `atoms` tables
- [ ] Logs aggregated (LOG_LEVEL=INFO; structured JSON to stdout)
- [ ] `alembic upgrade head` run during deploy

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Bot stays silent | Token wrong, or bot not started | `python -m app.bot.telegram_bot` and check stdout |
| `Connection refused localhost:11434` | Ollama not running | `ollama serve` |
| `model 'gemma4:e4b' not found` | Not pulled | `ollama pull gemma4:e4b` |
| Slow first reply | Cold model load | Set `OLLAMA_KEEP_ALIVE=-1` |
| `RuntimeError: cannot reuse already awaited coroutine` | Mixing sync + async DB session | Use `AsyncSession` only; never call `.commit()` on a sync session |
| MCP tool times out | Server crashed | Check `python -m app.mcp.<name>_server` stdout; restart |
| Tests hang on `pytest-asyncio` | Old plugin version | `pip install -U pytest-asyncio` (>=0.23) |
