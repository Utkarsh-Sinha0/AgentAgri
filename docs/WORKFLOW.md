# AgentAgri — Request Workflow

> A single farmer message, traced from Telegram tap to advisory reply. Use this as a map when debugging — every step here is something you can grep for in the codebase.

---

## The 14 steps, in order

### 1. Farmer sends a message
**Where:** Telegram client on the farmer's phone
**What:** Text, photo (with optional caption), or voice note (voice is roadmap, see [ROADMAP_STT_TTS.md](ROADMAP_STT_TTS.md))
**Example:** "सर मेरे धान में भूरे धब्बे आ रहे हैं पत्ती पे क्या करूं"

### 2. Telegram delivers the update
**Where:** `app/bot/telegram_bot.py`
**What:** `python-telegram-bot` long-polls Telegram's Bot API. When an update arrives, it routes to the right handler (`/start`, `/demo`, `/why <id>`, photo, plain text).
**Trace:** look for handler functions in `telegram_bot.py` matching the message type.

### 3. Bot resolves the farmer and active crop cycle
**Where:** `services/farmer_dashboard.py` + `models.py`
**What:** Looks up `Farmer` by Telegram user ID. If they don't exist, runs the onboarding flow (asks district → village → primary crop → sowing date → creates `Field` + `CropCycle`). Returns the active cycle so the agent has crop + stage context.

### 4. Bot creates an `Observation` row
**Where:** `app/bot/telegram_bot.py` → `models.Observation`
**What:** Persists the raw input *before* invoking the agent. This is the source-of-truth row that every downstream artefact (advisory, evidence card, memory atom, action impact) foreign-keys back to.
**Why it matters:** if the agent crashes mid-process, the observation survives for replay.

### 5. Bot builds `AgentContext` and calls `agent.process(db, ctx)`
**Where:** `services/agent.py` line ~80 (`async def process`)
**What:** The orchestrator entry point. Everything below happens inside one `process()` call.

### 6. Intent classification (grammar-constrained)
**Where:** `utils/ollama_client.py::classify_intent`
**What:** Ollama call with `intent_classification.schema.json` enforcing the output shape:
```json
{"intent": "disease_diagnosis", "needs_retrieval": true, "needs_tool_call": false, "language": "hi", "reason": "..."}
```
**Prompt:** `INTENT_CLASSIFICATION_PROMPT` (in `ollama_client.py`) — encodes the intent→retrieval routing policy explicitly so the model doesn't default to `needs_retrieval=false`.
**Outcome:** sets two booleans the next steps gate on: `needs_retrieval`, `needs_tool_call`.

### 7. Speculative retrieval (fired in parallel with steps 8–9)
**Where:** `services/retrieval.py::speculative_retrieve` → `retrieve`
**What:** If `needs_retrieval=True`, fire the retrieval task immediately (in parallel with tool planning) so we don't pay both latencies serially. Path:
  1. **SQL filter** — crop, stage, topic tags → candidate set
  2. **Graph expand** (if follow-up + we have prior article IDs) — pull ±1 hop from causes_of/aggravated_by/prevented_by/confused_with edges
  3. **BGE-M3 dense** — embed the query, score every candidate
  4. **bge-reranker-v2-m3** — cross-encoder rerank top-K → top-3
  5. **Load full articles** for the top-3 into the evidence bundle

### 8. Tool planning (ReAct, grammar-constrained)
**Where:** `utils/ollama_client.py::plan_tools` → `tool_call.schema.json`
**What:** If `needs_tool_call=True`, the model picks one or more MCP tools (`get_forecast`, `get_mandi_prices`, `get_msp`, `match_schemes`, `get_historical_weather`) with explicit arguments. Output is validated JSON, so we never pass garbage to MCP servers.

### 9. MCP tool execution
**Where:** `services/{weather,mandi,scheme,finance}.py` → `mcp_servers/*.py`
**What:** Each MCP server is a separate process on its own port (9001–9004). Clients in `services/` are thin async wrappers. If a server is down, `degradation.py` substitutes a safe fallback (e.g. "weather data unavailable, advising conservatively").

### 10. Memory recall
**Where:** `services/memory.py::retrieve_personal_memory` + `retrieve_similar_farm_context`
**What:** Two layers:
  - **M1 personal**: prior outcomes for *this* farmer on *this* field — "you tried copper oxychloride last kharif, rated 4/5"
  - **M3/M4 cross-farm**: anonymised patterns from similar farms in the district, gated by `is_shareable=True` (HIGH #5 fix, migration 0003). Requires ≥3 peer farmers to avoid identification.

### 11. Evidence bundle assembly
**Where:** `services/agent.py::_build_evidence_bundle`
**What:** Collects retrieved articles + tool results + memory context into an `EvidenceBundle` dataclass. Each item carries `source` ("wiki", "imd", "agmarknet", "memory") and `trust` ("high", "medium", "low") for the evidence-card system (E1).

### 12. Template selection (grammar-constrained)
**Where:** `utils/ollama_client.py::select_template` → `template_selection.schema.json`
**What:** The single biggest LLM call. Given the evidence bundle, the model picks:
  - `selected_action_indices` — which action numbers (from the wiki article's `actions` list) to recommend
  - `selected_warning_indices` — which warnings to include
  - `risk_level` — NORMAL / WATCH / PREVENTIVE_ACTION / ESCALATE
  - `confidence` — LOW / MEDIUM / HIGH
  - `contextualization` — Hindi explanation grounded in the evidence
**Why grammar-constrained:** the model can ONLY pick from indices that exist in the retrieved articles. It physically cannot invent an action.

### 13. Verifier
**Where:** `services/verifier.py::verify`
**What:** Four lines of defence — every one must pass or the advisory is rejected:
  1. **Structural** — indices in range, JSON valid, required fields present
  2. **Semantic** — recommended actions actually map to the diagnosed problem (e.g. ESCALATE doesn't get monitor-only actions — LOW #9 fix)
  3. **Safety** — no chemical dosages, no medical guarantees, no scheme-enrollment promises
  4. **Calibration** — confidence matches evidence strength
**On failure:** the advisory is NOT persisted (HIGH #4 fix); the no-evidence fallback path runs instead, and `retrieval_path="none"` is recorded for learning (LOW #10 fix).

### 14. Persistence + reply
**Where:** `services/agent.py::_persist_advisory` then back up to `bot/telegram_bot.py`
**What sequence:**
  1. Insert `Advisory` row (FK → observation)
  2. Insert `VerifierReport` row
  3. Insert `EvidenceCard` rows (one per evidence item, with source+trust)
  4. Run `extract_from_observation` → may create new `MemoryAtom` rows
  5. Insert `ConversationTurn` for follow-up detection on next message
  6. Insert `ActionImpact` placeholder rows so we can track outcomes when the farmer reports back
  7. `db.commit()`
  8. Format the Hindi reply string and `bot.send_message` back to the farmer
  9. If `risk_level == ESCALATE`, also `cluster.maybe_alert_extension_worker()`

---

## Visual: the 14-step pipeline

```
┌─────────┐
│ Farmer  │ "मेरे धान में भूरे धब्बे"
└────┬────┘
     │
     ▼
[1-2] Telegram update arrives → telegram_bot.py
     │
     ▼
[3-4] Resolve Farmer/CropCycle → INSERT Observation
     │
     ▼
[5]   agent.process(db, ctx)
     │
     ▼
[6]   classify_intent  ──┐ grammar-constrained
                          │ {"intent":"disease_diagnosis","needs_retrieval":true}
                          ▼
[7]   speculative_retrieve (fired in parallel with [8])
       │
       ├─► SQL filter (crop=rice, stage=vegetative)
       ├─► Graph expand (if follow-up)
       ├─► BGE-M3 dense embed
       └─► bge-reranker-v2-m3 → top-3
     │
     ▼
[8]   plan_tools  ──┐ grammar-constrained (if needs_tool_call)
                     ▼
[9]   MCP tool calls (weather / mandi / scheme / finance)
     │
     ▼
[10]  retrieve_personal_memory + cross-farm context
     │
     ▼
[11]  Assemble EvidenceBundle (with source + trust)
     │
     ▼
[12]  select_template  ──┐ grammar-constrained
                          │ {"action_indices":[0,1,4],"risk":"WATCH","conf":"MEDIUM",...}
                          ▼
[13]  verifier.verify (structural / semantic / safety / calibration)
     │
     ├─► PASS → step 14
     └─► FAIL → no-evidence fallback, no persist, retrieval_path="none"
     │
     ▼
[14]  Persist Advisory + VerifierReport + EvidenceCards + MemoryAtoms + ConversationTurn + ActionImpact
       │
       ▼
       Format Hindi reply
       │
       ▼
       bot.send_message(farmer, reply)
       │
       ▼
       (if ESCALATE) cluster.maybe_alert_extension_worker()
```

---

## Latencies (observed on gemma4:e2b-it-q4_K_M, CPU)

| Step | Wall time |
|---|---|
| Intent classification | 9–15 s |
| Retrieval (BGE-M3 + reranker, first call loads models) | 20–35 s cold, 1–2 s warm |
| Template selection (the big call) | 30–60 s |
| Verifier | 5–10 s |
| Persistence + reply | <0.5 s |
| **End-to-end p50** | **~67 s** |
| **End-to-end p95** | **~80 s** |

GPU + e4b model is expected to bring p50 under 15 s.

---

## Where to look when something breaks

| Symptom | First file to open |
|---|---|
| Bot doesn't respond at all | `app/bot/telegram_bot.py` — is the handler being hit? Check `TELEGRAM_BOT_TOKEN` |
| "I need more details" canned reply for every query | `services/agent.py` — search for `retrieval_path="none"`. The intent classifier returned `needs_retrieval=false` or retrieval returned 0 articles. |
| `XLMRobertaTokenizer has no attribute prepare_for_model` | `requirements.txt` — `transformers` version drift. Pinned to `4.49.0`. |
| `NOT NULL constraint failed: advisories.observation_id` | Caller forgot to create the `Observation` row before calling `agent.process`. Telegram bot does this; eval harness now does too. |
| `unsupported operand type(s) for -: 'list' and 'set'` | `services/retrieval.py:317` — graph traverse returned list; wrap in `set()` at call site. |
| Verifier rejects everything | `USE_GRAMMAR_DECODING=1` requires Ollama with grammar support. Check Ollama version (≥0.20.0). |
| Cross-farm context never appears | Need ≥3 peer farmers in same district with `is_shareable=True` atoms. By design. |

For end-to-end troubleshooting see [TODO_MAKE_IT_WORK.md](../TODO_MAKE_IT_WORK.md). For deep-dive on every feature see [FEATURES.md](FEATURES.md).
