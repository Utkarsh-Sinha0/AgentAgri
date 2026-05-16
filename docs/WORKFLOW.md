# AgentAgri — Workflows for Every Action

Every farmer-facing action mapped to code, with a mermaid diagram per flow. Companion to [ARCHITECTURE.md](ARCHITECTURE.md) (function map) and [FEATURES.md](FEATURES.md) (capability list).

---

## 0. Top-level request lifecycle

The shape every Telegram message follows once it reaches `AgentOrchestrator.process()` in `app/services/agent.py`.

```mermaid
flowchart TD
    A[Telegram update] --> B[telegram_bot.py handler]
    B --> C{Command?}
    C -- /cmd --> D[Command handler — DB ops + reply]
    C -- text/photo/voice --> E[AgentOrchestrator.process]
    E --> F[Step 0 Vision if photo]
    F --> G[Step 1 Intent classify]
    G --> H[Step 1.5 route_to_thread]
    H --> I[Step 2 Speculative retrieval START]
    I --> J[Step 3 ReAct plan + MCP tools parallel]
    J --> K[Step 4 await retrieval]
    K --> L[Step 5 EvidenceBundle assembly]
    L --> M[Step 6 Template select grammar-constrained]
    M --> N[Step 7 Verifier 4-line check]
    N -- pass --> O[Step 8 Display formatter]
    N -- fail --> M
    O --> P[Step 9 Persist atom + turn]
    P --> Q[Reply to farmer]
```

---

## 1. `/register` — onboarding a new farmer

```mermaid
sequenceDiagram
    participant F as Farmer
    participant T as telegram_bot
    participant S as services.farmer
    participant DB as Database
    F->>T: /register
    T->>F: Ask phone
    F->>T: phone
    T->>F: Ask name
    F->>T: name
    T->>F: Ask district
    F->>T: district
    T->>F: Ask tehsil (Bug 7 fix)
    F->>T: tehsil
    T->>F: Ask village
    F->>T: village
    T->>F: Ask primary crop + sowing date
    F->>T: rice, 2026-06-15
    T->>S: register_farmer(...)
    S->>S: hash_password(argon2)
    S->>DB: INSERT farmer + field + crop_cycle
    S-->>T: farmer_id
    T-->>F: Welcome + dashboard link
```

Files: `app/bot/telegram_bot.py::register_*`, `app/services/farmer.py::register_farmer`.

---

## 2. `/newcycle` — start a new crop cycle on an existing field

```mermaid
sequenceDiagram
    participant F as Farmer
    participant T as telegram_bot
    participant S as services.crop_cycle
    F->>T: /newcycle
    T->>F: Inline buttons — pick field
    F->>T: tap field
    T->>F: Ask crop name
    F->>T: tomato
    T->>F: Ask sowing date
    F->>T: 2026-05-20
    T->>S: create_cycle(field_id, crop, sowing_date)
    S->>S: close_active_cycle_if_any(field_id)
    S->>S: seed_calendar_tasks(crop, sowing_date)
    S-->>T: cycle_id + first 5 tasks
    T-->>F: Cycle created + this week's tasks
```

Files: `app/services/crop_cycle.py`, `app/services/calendar.py`.

---

## 3. Free-text question — the main agent loop

The path 90% of farmer messages take. Photo and voice converge here after their preprocessing.

```mermaid
flowchart TD
    A[Farmer: my tomato leaves have yellow spots] --> B[telegram_bot text handler]
    B --> C[AgentOrchestrator.process]
    C --> D[intent.classify]
    D -->|crop=tomato stage=fruiting tags=disease followup=false| E[route_to_thread]
    E -->|3-branch: continue / new / clarify| F[ConversationThread chosen]
    F --> G[asyncio.create_task retrieval]
    F --> H[react_plan: which MCP tools needed?]
    H -->|weather + scheme + mandi parallel| I[MCP gather]
    G --> J[BGE-M3 dense top-10]
    J --> K[BGE-reranker-v2-m3 top-3]
    I --> L[EvidenceBundle]
    K --> L
    L --> M[memory.recall: M1 decay + M2 outcome boost + M3 causal chain]
    M --> L
    L --> N[template_select with JSON grammar]
    N --> O[verifier 4-line]
    O -->|fails| N
    O -->|passes| P[E1 inline citations + E2 confidence words + E3 change-detect]
    P --> Q[ConversationTurn + Atom persisted]
    Q --> R[Reply sent]
```

---

## 4. Photo upload — Gemma vision + disease ID

```mermaid
sequenceDiagram
    participant F as Farmer
    participant T as telegram_bot
    participant V as services.vision
    participant O as ollama_client
    participant A as AgentOrchestrator
    F->>T: send photo of leaf
    T->>T: download largest photo
    T->>V: classify_image(bytes)
    V->>O: gemma4 with vision prompt
    O-->>V: {crop, symptoms, suspected_disease, confidence}
    V-->>T: VisionResult
    T->>A: process(text=caption, vision=VisionResult)
    A->>A: pipeline as in #3, with disease prior boosting retrieval
    A-->>T: advisory with photo-grounded citation
    T-->>F: Reply
```

Files: `app/services/vision.py`, `app/services/agent.py` Step 0.

---

## 5. Voice note — STT then agent

```mermaid
sequenceDiagram
    participant F as Farmer
    participant T as telegram_bot
    participant V as services.voice
    participant A as AgentOrchestrator
    F->>T: voice note (OGG opus)
    T->>V: transcribe(audio_bytes)
    V->>V: whisper / gemma-audio (ENABLE_VOICE_STT)
    V-->>T: text + lang_detected
    T->>A: process(text, lang=hi/en/hinglish)
    A-->>T: reply text
    T-->>F: Reply (text — TTS planned, see VISION.md)
```

Files: `app/services/voice.py`, `app/bot/telegram_bot.py::voice_handler`.

---

## 6. `/feedback` — farmer rates an advisory

```mermaid
sequenceDiagram
    participant F as Farmer
    participant T as telegram_bot
    participant S as services.feedback
    participant DB as Database
    F->>T: /feedback
    T->>F: Inline list — last 5 advisories
    F->>T: tap advisory
    T->>F: 1–5 stars
    F->>T: 4
    T->>F: optional comment
    F->>T: helped but late
    T->>S: record_feedback(turn_id, rating, comment)
    S->>DB: INSERT feedback + UPDATE atom.signals.feedback_avg
    S->>S: trigger memory rerank (M2 outcome boost)
    S-->>T: thanks
```

Files: `app/services/feedback.py`. Feeds the M2 outcome-boost signal in `app/services/memory.py`.

---

## 7. `/outcome` — farmer reports what happened

```mermaid
sequenceDiagram
    participant F as Farmer
    participant T as telegram_bot
    participant S as services.outcome
    participant P as services.pattern_discovery
    F->>T: /outcome
    T->>F: which advisory?
    F->>T: pick
    T->>F: result? worked / partial / failed
    F->>T: worked
    T->>S: record_outcome(turn_id, label, notes)
    S->>S: link to causal_predecessor atoms (M3)
    S->>P: maybe_discover_pattern(farmer_id, crop)
    P->>P: cluster recent atoms by symptom signature
    P->>P: write correlated_with edges (Bug 1 fix)
    S-->>T: logged + pattern hint if any
```

Files: `app/services/outcome.py`, `app/services/pattern_discovery.py`.

---

## 8. `/threads`, `/newthread`, `/endthread` — conversation routing

```mermaid
flowchart LR
    subgraph threads[/threads]
        A1[List active ConversationThreads] --> A2[Show last turn per thread]
    end
    subgraph newthread[/newthread]
        B1[End current active thread] --> B2[Create new thread] --> B3[Next msg seeds it]
    end
    subgraph endgraph[/endthread]
        C1[Mark active=false] --> C2[Next msg starts fresh]
    end
```

```mermaid
flowchart TD
    M[Incoming message] --> R[route_to_thread]
    R --> D{LLM decision}
    D -->|continue| K1[Use most-recent active thread]
    D -->|new_topic| K2[Create thread]
    D -->|ambiguous| K3[Ask: continuing the tomato chat or new question?]
    K1 --> X[record_turn with thread_id]
    K2 --> X
    K3 --> Y[Wait for farmer answer]
```

Files: `app/services/conversation.py::route_to_thread`, `record_turn`. Wiring tested in `tests/test_conversation_wiring.py`.

---

## 9. `/dashboard` — web infographic view

```mermaid
sequenceDiagram
    participant F as Farmer
    participant T as telegram_bot
    participant D as FastAPI dashboard
    participant FS as services.farmer_dashboard
    F->>T: /dashboard
    T-->>F: signed link http://host/d/<farmer_id>?t=<token>
    F->>D: GET /d/<farmer_id>
    D->>FS: build_dashboard(farmer_id)
    FS->>FS: aggregate fields + cycles + finance + recent advisories
    FS-->>D: DashboardContext
    D-->>F: HTML w/ charts (Chart.js)
```

Files: `app/api/dashboard.py`, `app/services/farmer_dashboard.py`, `app/templates/dashboard.html`.

---

## 10. MCP tool call — weather example

Same shape for mandi, scheme, finance — each is a standalone process.

```mermaid
sequenceDiagram
    participant A as agent.react_plan
    participant C as mcp_client
    participant W as weather_server :9001
    participant API as OpenWeather/IMD
    A->>C: call_tool('weather.forecast', district='Munger')
    C->>W: JSON-RPC
    W->>API: HTTPS
    API-->>W: JSON
    W->>W: normalize to schema
    W-->>C: ToolResult
    C-->>A: dict for EvidenceBundle
```

Files: `app/mcp/client.py`, `app/mcp/weather_server.py` (and `mandi_server.py`, `scheme_server.py`, `finance_server.py`).

---

## 11. Memory recall — M1 decay + M2 boost + M3 causal chain

```mermaid
flowchart TD
    Q[Query embedding] --> R[atoms WHERE farmer_id = ? ORDER BY embedding cosine]
    R --> S1[Score = cosine_sim]
    S1 --> S2[× temporal_decay age_days, half_life=30]
    S2 --> S3[+ outcome_boost feedback_avg, outcomes worked]
    S3 --> S4[Walk causal_predecessor edges depth=2]
    S4 --> S5[Add chain atoms to candidate pool]
    S5 --> X[Cross-farmer pool k-anonymity k>=5]
    X --> T[Top-N atoms]
    T --> U[Inject into EvidenceBundle]
```

Files: `app/services/memory.py` — functions `recall_with_decay`, `apply_outcome_boost`, `walk_causal_chain`, `cross_farmer_pool`.

---

## 12. Pattern discovery — background loop

```mermaid
flowchart TD
    O[/outcome recorded/] --> T[trigger maybe_discover_pattern]
    T --> C[cluster atoms by symptom_signature similarity > 0.85]
    C -->|cluster size >= 3| W[write Pattern atom]
    W --> E[for each pair: add correlated_with edge]
    E --> N[notify farmers in cluster on next msg]
    C -->|cluster size < 3| S[skip]
```

Files: `app/services/pattern_discovery.py`. Bug 1 ensured the edge label is `correlated_with`, not `causes_of`.

---

## 13. Verifier — the 4-line check before reply

```mermaid
flowchart TD
    D[Draft advisory] --> V[verifier prompt: 4 lines]
    V --> L1[1. Is every claim grounded in EvidenceBundle?]
    V --> L2[2. Are units / numbers correct?]
    V --> L3[3. Does confidence language match evidence strength?]
    V --> L4[4. Any unsupported recommendation?]
    L1 & L2 & L3 & L4 --> R{All pass?}
    R -- yes --> P[Proceed to display]
    R -- no --> F[Return failure reasons]
    F --> RG[Regenerate template with critic note]
    RG --> D
```

Bounded to 2 retries; on third failure the agent ships a hedged "I'm not sure — please call the local KVK" reply. Files: `app/services/verifier.py`.

---

## 14. Evidence assembly — what goes into a reply

```mermaid
flowchart LR
    W[Wiki passages top-3 reranked] --> EB[EvidenceBundle]
    WX[Weather forecast] --> EB
    M[Mandi prices] --> EB
    SC[Scheme matches] --> EB
    FI[Finance snapshot] --> EB
    ME[Memory atoms M1+M2+M3] --> EB
    NDVI[NDVI tile if available] --> EB
    EB --> CIT[E1 inline citations [W1] [Wx]]
    EB --> CONF[E2 confidence language by source strength]
    EB --> CHG[E3 change detection vs last reply]
```

Files: `app/services/agent.py::_build_evidence_bundle`, `app/services/evidence.py`.

---

## 15. Test & eval loop

```mermaid
flowchart LR
    PR[branch push] --> PT[pytest 216 tests]
    PT --> EV[python -m evals.run_eval --source synthetic]
    EV --> EX[--source farmer_qa]
    EX --> RM[manual review of regressions]
    RM --> MG[merge to main]
```

Files: `tests/`, `evals/run_eval.py`, `evals/cases/`.
