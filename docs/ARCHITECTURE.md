# AgentAgri — Architecture Map

> What lives where, and what each piece does in the request lifecycle. Read this *once* end-to-end before touching code. Specs and feature scope are locked — this doc explains the existing system, not changes to it.

---

## Top-level layout

```
AgentAgri/
├── app/                  ← all server code (FastAPI + Telegram + agent + MCP)
├── alembic/              ← database migrations (versioned)
├── data/                 ← runtime SQLite DB + seed JSON
├── docs/                 ← this folder (architecture, setup, workflow, etc.)
├── evals/                ← golden evaluation sets + per-run reports
├── pwa/                  ← farmer-facing web dashboard (optional, parallel to Telegram)
├── tests/                ← 211 regression tests
├── wiki/                 ← curated agronomy articles (markdown source for the wiki DB)
├── alembic.ini
├── docker-compose.yml
├── LICENSE
├── README.md
├── requirements.txt
└── .env.example
```

---

## `app/` — the brain of the system

```
app/
├── main.py                ← FastAPI app factory, lifespan, middleware, route mount
├── config.py              ← pydantic-settings — single source of truth for env vars
├── database.py            ← async SQLAlchemy engine, session_factory, init_db
├── models.py              ← Farmer, Field, CropCycle, Observation, Advisory, …
├── models_memory.py       ← MemoryAtom, ActionImpact, EvidenceCard, ConversationTurn
├── eval.py                ← legacy hand-curated eval set runner
├── eval_synthetic.py      ← synthetic 200-case eval runner (May 2026)
│
├── api/v1/                ← REST endpoints
│   └── auth.py            ← /login, /demo-session
│
├── bot/
│   └── telegram_bot.py    ← python-telegram-bot handlers (/start, /demo, /why, photo, text)
│
├── services/              ← business logic (each file = one responsibility)
│   ├── agent.py           ← the orchestrator — process(db, ctx) is the entry point
│   ├── retrieval.py       ← SQL filter → BGE-M3 dense → bge-reranker → top-3 articles
│   ├── verifier.py        ← 4-line check: structural, semantic, safety, calibration
│   ├── memory.py          ← M1–M4 memory atoms (per-farm + cross-farm with privacy filter)
│   ├── conversation.py    ← follow-up detection, action-impact graph, turn recording
│   ├── evidence.py        ← E1–E3 evidence cards (source + trust + provenance)
│   ├── cluster.py         ← geographic cluster detection for extension-worker alerts
│   ├── proactive.py       ← weather/IMD-based push alerts
│   ├── degradation.py     ← graceful fallback when Ollama / tools fail
│   ├── pattern_discovery.py ← unsupervised pattern mining over advisories
│   ├── alerts.py          ← outbound alert composition
│   ├── weather.py         ← MCP weather client
│   ├── mandi.py           ← MCP mandi client
│   ├── scheme.py          ← MCP scheme client
│   ├── finance.py         ← MCP finance client
│   ├── market_intel.py    ← derived market-trend logic
│   ├── farmer_dashboard.py ← PWA-facing aggregations
│   └── demo_seed.py       ← demo data seeding for hackathon flow
│
├── utils/
│   ├── ollama_client.py   ← every LLM call goes through here (chat, classify_intent, plan_tools, …)
│   ├── safety.py          ← PII redaction, content filters
│   ├── security.py        ← password hashing, API-key checks
│   └── time.py            ← utc_now() + tz helpers
│
├── schemas/               ← JSON schemas for grammar-constrained Ollama decoding
│   ├── intent_classification.schema.json
│   ├── tool_call.schema.json
│   ├── template_selection.schema.json
│   ├── safety_check.schema.json
│   └── cluster_summary.schema.json
│
└── mcp_servers/           ← Model Context Protocol servers (one per data source)
    ├── weather_server.py  ← port 9001
    ├── mandi_server.py    ← port 9002
    ├── scheme_server.py   ← port 9003
    └── finance_server.py  ← port 9004
```

### Service responsibilities in one line

| File | One-line role |
|---|---|
| `services/agent.py` | The conductor. Calls intent → retrieval → verifier → persist, in that order. |
| `services/retrieval.py` | Finds the 3 most relevant wiki articles for the farmer's question. |
| `services/verifier.py` | Rejects advisories that aren't grounded in retrieved evidence. |
| `services/memory.py` | Remembers what each farmer has seen + what worked for similar farmers. |
| `services/conversation.py` | Detects follow-up messages and links them to the prior advisory. |
| `services/evidence.py` | Stamps every advisory with source + trust-level provenance. |
| `services/cluster.py` | Groups same-issue reports across nearby farmers for extension workers. |
| `services/proactive.py` | Sends rain / heatwave warnings before the farmer asks. |
| `services/degradation.py` | Keeps the bot alive when Ollama / MCP servers are down. |
| `utils/ollama_client.py` | The only file that talks to Ollama. All prompts live in module-level constants here. |
| `schemas/*.schema.json` | Force Ollama output into validated JSON — no free-form hallucination at decision points. |
| `mcp_servers/*.py` | Each runs on its own port and exposes one external data source as a tool. |

---

## Data layer

### Tables (see `app/models.py` for full schemas)

| Table | Purpose |
|---|---|
| `farmers` | Phone, district, language preference, hashed password |
| `fields` | Per-farmer plot metadata (area, soil type, GPS) |
| `crop_cycles` | One row per sowing — links field, crop, sowing date, stage |
| `observations` | Every farmer message/photo/voice — pre-advisory raw input |
| `advisories` | Generated advisory + evidence pointer + verifier report |
| `verifier_reports` | Detailed 4-line verifier output for each advisory |
| `evidence_cards` | Source + trust per piece of evidence (wiki / weather / mandi / memory) |
| `memory_atoms` | M1 personal facts + M3/M4 sharable cross-farm patterns |
| `action_impacts` | What happened after the farmer followed (or didn't follow) the advice |
| `conversation_turns` | Multi-turn dialogue history for follow-up detection |
| `wiki_articles` | Indexed agronomy knowledge base (11 seeded, target 50+) |
| `clusters` | Same-issue groups across farms in same district |

### Migrations

`alembic/versions/` — 3 migrations currently applied:

```
0001_baseline.py         ← initial schema
0002_causal_chains.py    ← memory atom linkage
0003_atom_shareability.py ← privacy classification for cross-farm sharing
```

To apply on a fresh clone: `alembic upgrade head`.

---

## External integrations

```
                    ┌──────────────────────┐
                    │ Telegram Bot API     │  ← farmer's phone
                    └──────────┬───────────┘
                               │
            ┌──────────────────▼──────────────────┐
            │ app/bot/telegram_bot.py             │
            └──────────────────┬──────────────────┘
                               │
                  ┌────────────▼────────────┐
                  │  FastAPI  (app/main.py) │
                  └────────────┬────────────┘
                               │
          ┌────────────────────┼─────────────────────┐
          ▼                    ▼                     ▼
   services/agent.py    SQLite (data/)        Ollama (host:11434)
          │                                          │
          │                                  gemma4:e2b-it-q4_K_M
          ├────────► retrieval.py ──► BGE-M3 + bge-reranker-v2-m3
          │
          ├────────► MCP servers (ports 9001-9004)
          │             ├── weather_server.py   ← IMD / OpenWeatherMap (Phase 2)
          │             ├── mandi_server.py     ← agmarknet.gov.in (Phase 2)
          │             ├── scheme_server.py    ← PM-KISAN, RKVY catalogues
          │             └── finance_server.py   ← KCC, interest rates
          │
          └────────► verifier.py ──► reject or persist
```

---

## Key invariants (do not break)

1. **Every LLM call goes through `utils/ollama_client.py`.** Do not call `httpx.post(ollama_url)` from anywhere else.
2. **Decision points use grammar-constrained decoding.** If you add a new decision step (intent, plan, classify), add a schema in `app/schemas/` and route via `structured_chat()`.
3. **No advisory is persisted if the verifier rejects it** (`agent.py:261` enforces this).
4. **Citations use display index, not array index** (`agent.py:665` — drilled in during the HIGH #2 audit fix).
5. **Cross-farm memory retrieval respects `is_shareable`** (`memory.py:671`, migration 0003).
6. **MCP servers must be reachable on startup OR `degradation.py` masks them** — don't add hard imports.
7. **Tests are the contract.** 211 tests in `tests/`. Adding a feature without a regression test is not "done."

---

## Where new code goes

| You're adding... | Put it in... |
|---|---|
| A new LLM prompt | `utils/ollama_client.py` (module-level constant) + matching schema in `schemas/` |
| A new advisory step | `services/agent.py` — extend `process()` in dependency order |
| A new data source | `mcp_servers/<name>_server.py` + client in `services/<name>.py` |
| A new Telegram command | `bot/telegram_bot.py` — add a CommandHandler |
| A new DB column | New alembic migration, never edit a merged one |
| A new evaluation metric | `app/eval_synthetic.py` — add to `_aggregate()` |
| Domain knowledge | `wiki/articles/*.md` — re-seed with `make seed` |

---

For the request-by-request trace, see [WORKFLOW.md](WORKFLOW.md).
For setup, see [SETUP.md](SETUP.md).
For your task ownership as a collaborator, see [COLLABORATOR_GUIDE.md](COLLABORATOR_GUIDE.md).
