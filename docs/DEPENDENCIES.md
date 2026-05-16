# Dependencies — why each line of `requirements.txt` is there

Companion to [SETUP.md §8](SETUP.md#8-why-the-requirements-file-is-long). Every package in `requirements.txt`, what it does, and why this one and not an alternative.

The list reads long because AgentAgri is three projects stacked: a local ML stack, an async web + tool layer, and a farmer-language + media pipeline. Each pulls in its own toolchain — we picked battle-tested libraries and pinned exact versions for reproducibility.

---

## Core web

| Package | Version | Why this |
|---------|---------|----------|
| `fastapi` | 0.136.1 | First-class async, automatic OpenAPI for the dashboard API, Pydantic 2 native. Faster route dispatch than Flask + Quart, less ceremony than Starlette raw. |
| `uvicorn[standard]` | 0.46.0 | ASGI server; `[standard]` brings `httptools` and `uvloop` (Linux) for ~2× throughput. |
| `pydantic` | 2.13.4 | Schema validation across the agent — `EvidenceBundle`, `IntentResult`, MCP tool I/O. v2 Rust core is ~10× faster than v1, matters when every request validates 5+ models. |
| `pydantic-settings` | 2.14.1 | Reads `.env` into typed `Settings` in `app/config.py`. Replaces the deprecated `pydantic.BaseSettings`. |

---

## Database

| Package | Version | Why this |
|---------|---------|----------|
| `sqlalchemy` | 2.0.36 | Async ORM (`AsyncSession`) — single source of truth for `Farmer`, `Field`, `CropCycle`, `Atom`, `ConversationThread`. v2 has typed mappings, kills the legacy `Query` API. |
| `asyncpg` | 0.30.0 | Async Postgres driver. Fastest in Python (~3× psycopg2). Used in production via `postgresql+asyncpg://...`. |
| `psycopg2-binary` | 2.9.10 | Sync Postgres driver — used **only by Alembic**, which doesn't support async migrations yet. |
| `alembic` | 1.14.0 | Schema migrations. Every model change is a versioned, revertible revision — directly supports the reversibility rule. |

---

## LLM stack

| Package | Version | Why this |
|---------|---------|----------|
| `ollama` | 0.4.7 | Official Python client for the local Ollama daemon. Keeps farmer data on-device — non-negotiable. |
| `httpx` | 0.28.1 | Async HTTP. Used for MCP transport, external APIs (OpenWeather, AgMarknet), and as the transport under `ollama` when streaming. |

**Why not** OpenAI/Anthropic SDKs? Two reasons: (1) data sovereignty — farmers' messages cannot leave the device by default; (2) cost — at scale, per-token billing breaks the unit economics of an advisory bot for smallholders.

---

## Retrieval stack

| Package | Version | Why this |
|---------|---------|----------|
| `sentence-transformers` | 5.5.0 | Loads BGE-M3 as a dense encoder. Pure-Python, runs CPU or CUDA, no native compile. |
| `FlagEmbedding` | 1.4.0 | Official BGE family loader; required for the v2-m3 reranker which `sentence-transformers` doesn't ship directly. |
| `transformers` | 4.49.0 | Underlying tokenizer/model layer that both above libraries depend on. Pinned to the exact version both are tested against. |
| `torch` | 2.12.0 | Runtime for BGE. CPU build works on every dev box; CUDA build is a drop-in swap. ~800 MB but unavoidable for embeddings. |
| `lxml` | 6.1.0 | Fast XML/HTML parser for wiki article ingestion (`app/services/wiki.py`). C-extension, ~5× faster than stdlib `xml.etree`. |

**Why not** a hosted vector DB (Pinecone, Weaviate)? Same reason as the LLM — local. We store embeddings in Postgres `vector` columns or numpy on disk; query volume per farmer is low and the corpus is small (~5k wiki entries).

---

## MCP (Model Context Protocol)

| Package | Version | Why this |
|---------|---------|----------|
| `mcp` | 1.24.0 | Reference Python implementation of MCP — defines the protocol contract for tool servers. |
| `fastmcp` | 3.2.0 | Decorator-based server framework (think FastAPI for MCP). Each of our 4 servers (`weather`, `mandi`, `scheme`, `finance`) is ~80 lines because of it. |

---

## Telegram

| Package | Version | Why this |
|---------|---------|----------|
| `python-telegram-bot` | 21.10 | v21 is async-native and matches the rest of our async stack. Long-polling out of the box, webhook switch is one config change. Maintained, used in production by many bots. |

**Why not** `aiogram`? Comparable, but `python-telegram-bot` has wider community + better docs + first-class `ConversationHandler` (used during `/register` onboarding).

---

## Media

| Package | Version | Why this |
|---------|---------|----------|
| `Pillow` | 12.2.0 | Image decode/resize before sending to Gemma vision. Standard. |
| `opencv-python-headless` | 4.10.0.84 | Pre-vision preprocessing — crop, perspective correct, brightness normalize. `headless` variant avoids pulling GUI libs (saves ~200 MB, allows Docker slim images). |

Whisper STT is commented out in `requirements.txt` — see VISION.md for the planned reintroduction (Whisper-tiny or distil-whisper for low-RAM hosts).

---

## NLP / language

| Package | Version | Why this |
|---------|---------|----------|
| `indic-nlp-library` | 0.92 | Devanagari + Indic-script tokenization. Needed for Hindi/Bhojpuri text and for the planned Hinglish (Latin-script Hindi) normalizer. |
| `langdetect` | 1.0.9 | Three-line language ID. Routes farmer messages between Hindi, English, and Hinglish prompts. |

---

## Auth / security

| Package | Version | Why this |
|---------|---------|----------|
| `argon2-cffi` | 23.1.0 | Argon2id password hashing — winner of the Password Hashing Competition, OWASP-recommended over bcrypt. |
| `python-multipart` | 0.0.28 | Form-data parsing for dashboard uploads (photo, voice). Required by FastAPI for `Form()` / `File()`. |

---

## Caching / rate limit / IO

| Package | Version | Why this |
|---------|---------|----------|
| `redis` | 5.2.1 | Rate limiter backing (`app/utils/rate_limit.py`) and short-lived session store. |
| `aiofiles` | 24.1.0 | Async file IO for photo / voice uploads. Avoids blocking the event loop on disk reads. |
| `orjson` | 3.11.6 | JSON encode/decode in Rust; ~3× stdlib speed. Used for MCP payloads + Telegram response serialization. |

---

## Resilience / DX

| Package | Version | Why this |
|---------|---------|----------|
| `tenacity` | 9.0.0 | Retry decorator with exponential backoff. Wraps every external call (Ollama, MCP, OpenWeather) — bot stays up when one tool flakes. |
| `loguru` | 0.7.3 | Structured logging with zero config. Drop-in replacement for stdlib `logging`. |
| `python-dotenv` | 1.2.2 | Loads `.env` for non-Pydantic scripts (eval runner, seed scripts). |
| `ruff` | 0.8.6 | Linter + formatter. Pinned because CI lints with this exact version. |

---

## Testing

| Package | Version | Why this |
|---------|---------|----------|
| `pytest` | 9.0.3 | Standard. |
| `pytest-asyncio` | 1.3.0 | `asyncio_mode = auto` lets us write `async def test_*` without per-test markers — 216 of our tests use it. |
| `pytest-cov` | 7.1.0 | Coverage reporting. Not required to run tests, gated to CI. |

---

## Commented out (intentionally)

| Line | Why disabled |
|------|--------------|
| `openai-whisper` | Build issues on Python 3.13, feature-flagged off in `ENABLE_VOICE_STT`. To be replaced with `faster-whisper` (CTranslate2) or distil-whisper — see VISION.md §STT. |
| `preact`, `tailwindcss` | Frontend PWA — built via `npm`, not `pip`. |

---

## Reading the version pins

Every version is exact (`==`), not range. Three reasons:

1. **Reproducibility** — same `requirements.txt` → same wheel set → same behaviour.
2. **ML stack fragility** — `torch` × `transformers` × `sentence-transformers` × `FlagEmbedding` have known compatibility matrices. A `>=` would let pip pick incompatible combos.
3. **Audit trail** — when a regression appears, `git blame requirements.txt` tells you the exact bump.

Bumping a dependency is a standalone commit, run the full test suite + eval before merging.

---

## Adding a new dependency — checklist

1. Does an existing dep already cover it? (Don't add `requests` if `httpx` is already in.)
2. Is it actively maintained? Last release in the last 12 months.
3. Is the license permissive? (MIT / Apache / BSD — avoid GPL.)
4. Pin the exact version (`==`).
5. Add a row to this file under the right section.
6. Run `pytest tests --ignore=tests/test_agent_e2e.py` — must stay green.
