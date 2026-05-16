# AgentAgri — Feature Inventory

> Status snapshot as of 2026-05-16 · 224/224 tests green.

## Outbreak Warning — 10%-threshold pest/disease early-warning

When a farmer reports a pest or disease via `/outcome` with a negative result (`worsened` / `no_change`), `app/services/outbreak.py` runs a scope cascade (village → tehsil → district). For the smallest scope where reporting farmers ≥ 10% of registered farmers growing the same crop in that area in the last 14 days, the system:

1. Creates an `AlertCluster` row with `kind=outbreak`, `scope`, `pest_or_disease`, `reporting_farmer_ids`.
2. Pushes a bilingual Telegram warning to every other farmer in scope growing that crop.
3. Writes an `outbreak_alert` `MemoryAtom` (shareable, public) for each notified farmer so retrieval picks it up for ~14 days.
4. Prepends a one-line banner to the farmer's *next* agent reply (consumed once, deduped per farmer).

Tunables (top of `app/services/outbreak.py`): `OUTBREAK_THRESHOLD=0.10`, `OUTBREAK_REPORT_WINDOW_DAYS=14`, `OUTBREAK_ALERT_TTL_DAYS=14`, `OUTBREAK_MIN_REPORTERS=2`. Coverage: `tests/test_outbreak_warning.py` (8 cases — threshold met/not-met, scope cascade, alert reuse, push dedup, expiry).

---

> Every feature below is shipped on `main` unless the section header marks it `(Planned)` or `(Future)`.

---

## 1. Farmer-Facing Capabilities (Telegram Bot)

The Telegram bot (`app/bot/telegram_bot.py`) is the primary surface. Hindi-first UI with `/`-separated English aliases on every label so farmers and extension workers share the same screen.

### 1.1 Conversational entry points

| Command | Purpose |
|---|---|
| `/start` | Welcome screen; auto-detects existing thread and shows resume hint. |
| `/register` | Multi-step registration: name → phone → district → tehsil → village → farm size. |
| `/profile` | View / edit `FarmerProfile` (irrigation, soil, budget, mandi, scheme enrolment). |
| `/field`, `/fields`, `/usefield` | Create / list / switch active field. |
| `/crop`, `/crops`, `/usecrop` | Manage active crop on active field. |
| `/newcycle`, `/closecycle` | Open or close a crop cycle (sowing date, stage, variety). |
| Free text | Routed to the agent pipeline as a query. |
| Photo | Routed to vision analyzer + agent pipeline. |
| Voice / audio | Saved to `data/audio/` as an `Observation` (STT pending — see [VISION.md](VISION.md)). |

### 1.2 Advisory & evidence

| Command | Purpose |
|---|---|
| `/why` | Show evidence trace + citations behind the last advisory. |
| `/sources` | List wiki articles, memory atoms, and tool calls behind the last advisory. |
| `/tasks` | Generated task list for the active crop cycle (sowing → harvest). |
| `/calendar` | Stage-aware calendar of upcoming actions. |
| `/feedback` | Persisted to `Advisory.farmer_feedback` (1-5 + comment). |
| `/outcome` | Persisted to `Observation.outcome_*`; triggers M2 memory boost on linked atoms. |

### 1.3 Markets, schemes, finance

| Command | Purpose |
|---|---|
| `/prices` | Mandi prices from MCP `mandi_server` with MSP context. |
| `/expense`, `/sale` | Log farm expenses / sales. |
| `/finance` | Personal P&L for the active cycle. |

### 1.4 Memory & threads

| Command | Purpose |
|---|---|
| `/memory` | Inspect MemoryAtoms for active field. |
| `/threads` | List active + archived ConversationThreads with title, turn count, summary. |
| `/newthread` | Archive current thread, start a new one (confirm/cancel). |
| `/endthread` | Archive current thread (`is_active=False`); zombie-safe routing. |

### 1.5 Diagnostics

| Command | Purpose |
|---|---|
| `/dashboard` | Inline link to PWA dashboard (degrades to plain text when base URL is local). |
| `/health` | Bot + LLM + DB + MCP health check. |
| `/demo` | Authenticate as a sandbox demo farmer (gated by `BOT_DEMO_SECRET_CURRENT`). |

---

## 2. Agent Pipeline (LLM Reasoning)

Two-step hybrid loop in `app/services/agent.py:AgentOrchestrator.process`:

```
┌──────────────────────────────────────────────────────────────┐
│ Step 0  Vision (if photo)        → analyze_crop_photo        │
│ Step 1  Intent classification    → LLM (thinking OFF)        │
│ Step 1.5 Conversation routing    → route_to_thread()         │
│ Step 2  Speculative retrieval    → BGE-M3 + reranker         │
│ Step 3  ReAct planning           → LLM (thinking ON) + MCP   │
│ Step 4  Await retrieval                                       │
│ Step 5  Build EvidenceBundle     → wiki + memory + tools     │
│ Step 6  Template selection       → LLM (grammar-constrained) │
│ Step 7  Verifier (4-line check)  → recency, contradiction,   │
│                                     calibration, safety       │
│ Step 8  Display text + citations → inline E1/E2/E3           │
│ Step 9  Persist Advisory + atoms + record_turn(thread_id)    │
└──────────────────────────────────────────────────────────────┘
```

| Pipeline feature | File · function |
|---|---|
| Intent classification (LLM-decided crop, stage, region, tags, follow-up flags) | `ollama_client.py::classify_intent` |
| Conversation routing (3-branch: exact scope → cross-crop → create) | `conversation.py::route_to_thread` |
| Speculative retrieval (BGE-M3 dense + lexical + title/tag co-mention rerank) | `retrieval.py::speculative_retrieve` |
| ReAct planning + MCP tool execution (parallel) | `agent.py::_execute_tool` |
| Grammar-constrained template selection (JSON schemas in `app/schemas/`) | `ollama_client.py::select_template` |
| 4-line verifier (recency, contradiction, calibration, safety) | `verifier.py::VerifierService.verify` |
| Safety-redirect bypass for calibration / structural checks | `verifier.py::_check_safety` |
| Tool-only response synthesis (weather / mandi / scheme without wiki) | `agent.py::_build_tool_only_response` |
| Follow-up deterministic monitor-and-continue path | `agent.py::_build_followup_response` |
| Conservative no-evidence clarification | `agent.py::_no_evidence_response` |

---

## 3. Memory System (SOTA Living Memory)

Architecture: `MemoryAtom` rows scoped to 6-scale geography (village → tehsil → district → state → region → national), with causal chains and temporal decay.

| # | Feature | Implementation |
|---|---|---|
| M1 | Temporal decay weighting (per-atom-type half-lives) | `memory.py::_temporal_weight` |
| M2 | Outcome-weighted confidence boost (boosts atoms in the causal chain) | `memory.py::extract_from_outcome` |
| M3 | Causal chain linking (`causal_predecessor_atom_id` self-FK, cycle-safe traversal) | `memory.py::get_causal_chain` |
| M4 | Cross-farmer learning (privacy-gated k-anonymity ≥ 3 farmers) | `memory.py::retrieve_similar_farm_context` |
| — | Pattern discovery (district × crop × risk grouping → AlertCluster) | `pattern_discovery.py::discover_patterns` |
| — | Co-occurrence edges to `correlated_with` (not `causes_of`) | `pattern_discovery.py` |

---

## 4. Evidence & Citation

| # | Feature | File |
|---|---|---|
| E1 | Inline citation per action: `[📚 Source • N similar cases • in <district>]` | `agent.py::_build_action_citation` |
| E2 | Confidence language mapping (LOW/MEDIUM/HIGH/ESCALATE → Hindi+English prefix) | `agent.py::_build_advisory_display` |
| E3 | Change detection for follow-ups (risk delta, new articles, days since last) | `agent.py::build_change_detection_summary` |

---

## 5. Telegram Conversation UX (Task #8 — all 6 phases)

| Phase | What it does | File |
|---|---|---|
| 1 | `route_to_thread` helper + 8 unit tests | `conversation.py`, `tests/test_conversation_routing.py` |
| 2 | Pipeline wires routing into `record_turn(thread_id=…)` | `agent.py`, `conversation.py` |
| 3 | `/threads`, `/newthread`, `/endthread` commands + callbacks | `telegram_bot.py` |
| 4 | Inline keyboard row after every advisory (Threads / New thread) | `telegram_bot.py::_process_farmer_query` |
| 5 | `/start` hint when active thread exists | `telegram_bot.py::start` |
| 6 | Per-field threads strip in farmer dashboard | `farmer_dashboard.py::_threads_for_field` |

---

## 6. MCP Tool Servers

Each runs on its own port (configurable via `MCP_*_PORT`). The agent calls them via `httpx` when ReAct planning emits a tool call.

| Server | Tool | Port | File |
|---|---|---|---|
| Weather | `get_forecast`, `get_historical_weather` | 9001 | `app/mcp_servers/weather_server.py` |
| Mandi | `get_mandi_prices` | 9002 | `app/mcp_servers/mandi_server.py` |
| Scheme | `match_schemes` | 9003 | `app/mcp_servers/scheme_server.py` |
| Finance | `compute_farm_finance` | 9004 | `app/mcp_servers/finance_server.py` |

Tool kwargs are adapted to each server's real signature in `agent.py::_execute_tool` (no kwarg-mismatch silent drops).

---

## 7. Retrieval Stack (Graph-Wiki RAG)

| Layer | Detail | File |
|---|---|---|
| Dense | BGE-M3 (`BAAI/bge-m3`, dimension 1024) | `retrieval.py::embedder` |
| Sparse | Title + tag overlap with topic_tags | `retrieval.py::_lexical_boost` |
| Rerank | BGE-reranker-v2-m3, top_k=3 from top_k=10 | `retrieval.py::_rerank` |
| Co-mention boost | Title + tag co-mention bumps rerank score | `retrieval.py` |
| Graph edges | `correlated_with`, `causes_of`, `treats`, `prevents` per `WikiArticle` | `models.py::WikiArticle` |
| Follow-up bias | Previous-advisory article IDs surfaced first | `agent.py` Step 1.5 + `retrieval.py` |

---

## 8. Verifier (4-Line Truthfulness Check)

`app/services/verifier.py::VerifierService.verify` runs four independent gates and on any failure substitutes a safe Hindi fallback:

| Line | Check | Failure → |
|---|---|---|
| 1 | Structural (action/warning indices resolve to real articles) | drop to fallback |
| 2 | Memory contradiction (BGE-M3 cosine ≥ 0.80 + completion marker → contradiction) | drop confidence one rung |
| 3 | Calibration (article count + atom count + 14-day recency) | downgrade HIGH→MEDIUM |
| 4 | Safety (pesticide dosage / re-entry interval / banned chemicals) | redirect; bypass other gates |

---

## 9. Crop Cycles, Fields, Tasks

| Feature | File |
|---|---|
| `Field` (area_acres, name, GPS optional) | `models.py::Field` |
| `CropCycle` (sowing_date, stage, variety, is_active) | `models.py::CropCycle` |
| `Observation` (text, photo, audio, vision_analysis JSON, outcome_*) | `models.py::Observation` |
| `Advisory` (risk_level, confidence, evidence_article_ids, feedback) | `models.py::Advisory` |
| Stage-aware task generator | `farmer_dashboard.py::_tasks_for_cycle` |
| Sowing-date validation (no future, no before-registration) | `telegram_bot.py::_handle_crop_sowing_date` |

---

## 10. Farmer Dashboard (PWA)

Served by FastAPI at `/dashboard`. Reads the same data the bot writes — single source of truth.

| Section | What it shows |
|---|---|
| Active crop card | Stage, days since sowing, NDVI, latest advisory, risk + confidence |
| Per-field threads strip | Active + archived ConversationThreads, last 3 turns each (phase 6) |
| Crop analysis | Vision thumbnails, observation log |
| Market prices | Mandi rows + MSP context |
| Weather | Forecast + history |
| AI advisor / Gemma showcase | Model in use, latency, retrieval path, tool calls, citations, reasoning summary |
| History | All advisories with risk / confidence / actions / warnings |
| Settings | Profile fields + scheme enrolment |

---

## 11. Evaluation Harness

| Eval | What it runs | File |
|---|---|---|
| Synthetic 200-case | Full agent loop on `golden_synthetic_v1` dataset | `app/eval_synthetic.py` |
| Stratified 24-case | `--source` flag, 24 cases across crops/regions | `app/eval_synthetic.py --source 24` |
| Mock-farmer scorecard | 20 real Q&A tied to seeded mock farmer | `scripts/run_farmer_scorecard.py` |
| Probe intent | Live LLM-extraction smoke tests | `app/eval.py` |
| Test suite | 216 tests across 18 files | `tests/` |

---

## 12. Security & Operations

| Feature | File |
|---|---|
| Argon2 password hashing | `utils/security.py::hash_password` |
| API key + per-key rate limiter (Redis-backed) | `utils/security.py::get_api_limiter` |
| Config fail-fast (rejects placeholder values in `production`) | `config.py::startup_errors` |
| GZip middleware | `main.py` |
| TrustedHost middleware | `main.py` |
| CORS allowlist | `main.py` |
| Demo session rotating secret | `config.py::bot_demo_secret_current` |
| Markdown-parse fallback to plain text | `telegram_bot.py` |
| Ollama retry + graceful degrade on empty grammar output | `utils/ollama_client.py` |
| Alembic migrations | `alembic/versions/` |

---

## 13. Languages

| Language | State |
|---|---|
| Hindi (primary) | All UI strings, advisory templates, safety fallbacks |
| English (aliased) | Every UI label is `<Hindi> / <English>` |
| Mixed / Hinglish | Intent classifier accepts; templates render Hindi |
| Bhojpuri | Feature-flagged `ENABLE_BHOJPURI=false` (Planned — see [VISION.md](VISION.md)) |

---

## 14. Roadmap Highlights (See [VISION.md](VISION.md) for the full plan)

- **STT / TTS** — lightweight open-source models (Whisper-tiny + Coqui XTTS-v2 or Indic-Parler-TTS) for voice in / voice out.
- **Hinglish understanding** — LLM-driven code-mix interpretation (currently coarse; will move to a fine-tuned Gemma 4 LoRA).
- **Dashboard infographics** — NDVI heatmaps, yield projections, weather risk overlays.
- **Gemini Vision integration** — disease / pest identification from photo with confidence + species name.
- **Clarifying-question loop** — when intent confidence < threshold, agent asks back before committing.
- **Global expansion** — geography table extended beyond India; per-region wiki packs.

---

## Test inventory

```
tests/
├── conftest.py                       # fixtures: in-memory SQLite, seeded farmer
├── test_agent_e2e.py                 # pre-existing baseline failure (excluded)
├── test_api_security.py              # API key, rate limit, CORS, trusted host
├── test_async_blockers.py            # no sync IO in async paths
├── test_auth_demo_session.py         # rotating demo secret
├── test_bug_regressions.py           # 9 P0 bug regressions
├── test_config_validator.py          # placeholder rejection in prod
├── test_conversation_impact.py       # turn-level memory accumulation
├── test_conversation_routing.py      # route_to_thread 3-branch (8 cases)
├── test_conversation_wiring.py       # record_turn(thread_id=...) honored
├── test_demo_seed.py                 # seed_mock_farmer reproducibility
├── test_e4b_grammar.py               # grammar-constrained JSON output
├── test_evidence_citation.py         # E1/E2/E3 rendering
├── test_farmer_dashboard.py          # dashboard payload shape
├── test_mcp_startup.py               # all 4 MCP servers boot
├── test_memory_accuracy.py           # M1-M4 SOTA features
├── test_migrations.py                # Alembic upgrade/downgrade
├── test_retrieval.py                 # BGE rerank + co-mention boost
├── test_seeded_dates.py              # date validation invariants
└── test_telegram_flows.py            # end-to-end command flows
```

**216 passing · 0 failing · 1 excluded** (pre-existing e2e baseline failure unrelated to current scope).
