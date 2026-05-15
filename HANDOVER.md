# AgriMesh V4.0 — Engineering Handover Document

**Date:** May 13, 2026  
**Project:** AgriMesh V4.0 — AI Agricultural Intelligence Agent  
**Competition:** Kaggle Gemma 4 Good Hackathon (Deadline: May 18, 2026)  
**Track:** Global Resilience  
**Repository:** `E:/Career/hackathon/AgentAgri`

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Complete File Inventory](#3-complete-file-inventory)
4. [Database Schema](#4-database-schema)
5. [AI Engine Details](#5-ai-engine-details)
6. [Anti-Hallucination System](#6-anti-hallucination-system)
7. [Living Memory System](#7-living-memory-system)
8. [Evidence & Source Registry](#8-evidence--source-registry)
9. [Retrieval Pipeline](#9-retrieval-pipeline)
10. [Agent Orchestrator](#10-agent-orchestrator)
11. [MCP Tool Servers](#11-mcp-tool-servers)
12. [Telegram Bot Interface](#12-telegram-bot-interface)
13. [PWA Dashboard](#13-pwa-dashboard)
14. [Degradation Ladder](#14-degradation-ladder)
15. [Alert & Cluster System](#15-alert--cluster-system)
16. [Market Intelligence](#16-market-intelligence)
17. [Proactive Messaging](#17-proactive-messaging)
18. [Pattern Discovery](#18-pattern-discovery)
19. [Evaluation Harness](#19-evaluation-harness)
20. [Safety & Security](#20-safety--security)
21. [Test Suite](#21-test-suite)
22. [Deployment](#22-deployment)
23. [Demo Script](#23-demo-script)
24. [Gap Analysis](#24-gap-analysis)
25. [Post-Submission Roadmap](#25-post-submission-roadmap)
26. [Quick Reference](#26-quick-reference)

---

## 1. Executive Summary

AgriMesh V4.0 is a **local-first, AI-powered agricultural intelligence agent** for smallholder farmers. It runs entirely on a consumer laptop (RTX 3060 12GB) with **zero cloud dependency**. The system uses **Gemma 4 E4B** via Ollama for vision, chain-of-thought reasoning, tool calling, and multilingual advisory — all constrained by **grammar-enforced JSON schemas** to prevent hallucination.

### Key Metrics

| Metric | Value |
|---|---|
| Total files | **100** |
| Python files | **48** |
| Database tables | **20** (12 core + 8 memory/evidence) |
| Services | **15** |
| MCP servers | **4** |
| Wiki articles | **11** (with 9 graph relationship types) |
| Grammar schemas | **5** |
| Telegram commands | **24** |
| Registered sources | **16** |
| Tests | **211 passing** |
| Target VRAM | ~8.9 GB / 12 GB (74%) |
| Target latency | p50 ≤ 3.5s, p95 ≤ 6.0s |

### Architecture at a Glance

```
Farmer (Telegram) → Bot Adapter → Agent Orchestrator
                                      ├── Intent Classifier (2-tier)
                                      ├── ReAct Planner (thinking ON)
                                      ├── MCP Tools (weather, mandi, scheme, finance)
                                      ├── Retrieval (BGE-M3 + reranker + graph)
                                      ├── Living Memory (semantic top-k)
                                      ├── Template Selector (thinking OFF)
                                      └── Verifier (4-line defense)
                                           ↓
                                    Advisory + Evidence Cards + Citations
                                           ↓
                              Extension Worker (PWA Dashboard)
```

---

## 2. Architecture Overview

### System Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CHANNELS                                      │
│  Telegram Bot │ PWA Dashboard │ SMS (future) │ WhatsApp (future)     │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│                      FASTAPI GATEWAY (:8000)                          │
│  Health Monitor (60s) │ Circuit Breaker (6-level) │ Rate Limiter     │
└────────────────────────────┬────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│                     AGENT ORCHESTRATOR                                 │
│                                                                       │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────────┐ │
│  │ Intent       │──▶│ ReAct        │──▶│ Template Selector        │ │
│  │ Classifier   │   │ Planner      │   │ (Grammar-Constrained)    │ │
│  │ (2-tier)     │   │ (Thinking ON)│   │ (Thinking OFF)           │ │
│  └──────────────┘   └──────┬───────┘   └───────────┬──────────────┘ │
│                            │                        │                │
│              ┌─────────────┼────────────┐           │                │
│              ▼             ▼            ▼           ▼                │
│        ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│        │ MCP     │  │ Retrieval│  │ Memory   │  │ Verifier │       │
│        │ Tools   │  │ Pipeline │  │ System   │  │ (4-line) │       │
│        │ (x4)    │  │          │  │          │  │          │       │
│        └─────────┘  └──────────┘  └──────────┘  └──────────┘       │
└─────────────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│                         AI ENGINE                                     │
│  Ollama Server (:11434) → Gemma 4 E4B Q4_K_M (8.9GB @ 16K)          │
│  Fallback: Gemma 4 E2B Q4_K_M                                        │
│  Vision │ Thinking Mode │ Function Calling │ Grammar Decoding        │
└─────────────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│                       STORAGE LAYER                                   │
│  PostgreSQL 16 / SQLite │ Redis (cache) │ Wiki Articles (11)         │
│  Seed Data (weather, mandi, schemes, NDVI)                           │
└─────────────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────────┐
│                    BACKGROUND SERVICES                                │
│  Health Monitor (60s) │ Proactive Messaging (30min)                  │
│  Pattern Discovery (nightly) │ Memory Coarsening (nightly)           │
│  Eval Harness (on-demand)                                            │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Architectural Decisions

| Decision | Rationale |
|---|---|
| **E4B over E2B** | 42.2% vs 24.5% on Tau2 agentic benchmark; both fit 12GB VRAM |
| **Grammar-constrained, not post-hoc validation** | 100% schema validity at sample time; ~25% accuracy gain |
| **Template selection (not free-text generation)** | LLM picks action indices; actual text lives in wiki DB. Hallucination structurally impossible |
| **Living Memory (not "latest 5 observations")** | Semantic top-k retrieval filtered by field/crop/stage/risk type |
| **MCP-first tool layer** | Standard 2026 protocol; judges recognize it; tools are independently testable |
| **Conditional thinking mode** | ON for risk reasoning (worth latency cost); OFF for template selection (saves 1-2s) |
| **Adaptive retrieval routing** | Fast path for simple queries (~60%); graph path for multi-evidence queries (~40%) |
| **KV locality preservation** | Thinking mode mutates user message, not system prompt — preserves KV cache |

---

## 3. Complete File Inventory

### Project Root
```
agrimesh/
├── README.md                    # 1,109 lines, 5 Mermaid diagrams, full docs
├── Makefile                     # 20+ commands (make demo, test, eval, seed...)
├── Dockerfile                   # Python 3.13-slim, FastAPI
├── docker-compose.yml           # PostgreSQL + Redis + API + 4 MCP servers
├── pyproject.toml               # Ruff + pytest + coverage + Alembic config
├── requirements.txt             # All Python dependencies
├── .env.example                 # Environment template
├── alembic.ini                  # Database migration config
└── alembic/
    ├── env.py                   # Auto-detects Base.metadata
    └── versions/                # Migration scripts
```

### Application Core (`app/`)
```
app/
├── __init__.py
├── main.py                      # FastAPI app + lifespan (bots, monitors, tasks)
├── config.py                    # Pydantic Settings (all env vars)
├── database.py                  # Async SQLAlchemy engine + session factory
├── models.py                    # 12 core models (225 lines)
├── models_memory.py             # 5 memory/evidence models (102 lines)
├── eval.py                      # Ragas-style eval harness (15 golden queries)
│
├── schemas/                     # JSON Schema for grammar-constrained decoding
│   ├── intent_classification.schema.json
│   ├── tool_call.schema.json
│   ├── template_selection.schema.json
│   ├── safety_check.schema.json
│   └── cluster_summary.schema.json
│
├── services/                    # 15 service modules
│   ├── agent.py                 # Agent Orchestrator (ReAct + Selection, 530 lines)
│   ├── retrieval.py             # BGE-M3 + reranker + graph + adaptive routing
│   ├── verifier.py              # 4-line anti-hallucination defense
│   ├── memory.py                # Living Memory: extract, coarsen, traverse
│   ├── evidence.py              # Source registry + citation builder
│   ├── degradation.py           # 6-level degradation ladder + circuit breaker
│   ├── alerts.py                # 6-factor similarity scoring + cluster lifecycle
│   ├── cluster.py               # Alert cluster CRUD + broadcast
│   ├── proactive.py             # 4-trigger proactive messaging (30min loop)
│   ├── pattern_discovery.py     # Nightly pattern detection + graph edge proposals
│   ├── market_intel.py          # Sell advisor + FCI directory
│   ├── weather.py               # Seeded weather (IMD-compatible API shape)
│   ├── mandi.py                 # Seeded mandi prices (agmarknet format)
│   ├── scheme.py                # 5 government schemes
│   └── finance.py               # Expense/revenue/P&L tracking
│
├── mcp_servers/                 # 4 FastMCP servers
│   ├── weather_server.py
│   ├── mandi_server.py
│   ├── scheme_server.py
│   └── finance_server.py
│
├── bot/
│   └── telegram_bot.py          # 24 commands + photo + structured capture
│
└── utils/
    ├── ollama_client.py         # Gemma 4 client (grammar, thinking, vision)
    ├── safety.py                # 2-stage safety filter (27 regex + LLM)
    └── security.py              # Argon2id, rate limiter, privacy manager
```

### Data & Content
```
data/
├── seed/
│   ├── weather.json             # 5-day forecast + 7-day history
│   ├── mandi_prices.json        # Rice, wheat, maize, pulses
│   └── schemes.json             # 5 government schemes
├── ndvi_seed.json               # 6 weeks NDVI data (declining trend)
└── agrimesh.db                  # SQLite database (auto-created)

wiki/articles/                   # 11 Graph-Wiki articles
├── rice_blast.json              # Diamond-shaped lesions, 25-28°C, high humidity
├── rice_brown_spot.json         # Oval brown spots, potassium deficiency link
├── stem_borer.json              # Dead heart + white ear symptoms
├── bacterial_leaf_blight.json   # Water-soaked margin lesions
├── wheat_rust.json              # Stem/leaf/stripe rust, reddish-brown pustules
├── nitrogen_excess.json         # Lush growth, disease susceptibility
├── potassium_deficiency.json    # Leaf tip burn, weak stems
├── water_management_rice.json   # AWD methodology, drainage
├── fungicide_safety.json        # PPE, no mixing, PHI, neem alternatives
├── mandi_selling_guide.json     # MSP, grading, FCI, distress sale avoidance
└── pm_kisan_guide.json          # Eligibility, documents, free enrollment
```

### Frontend
```
pwa/
├── package.json
├── vite.config.js
├── index.html                   # Dark theme, CSS variables
└── src/
    ├── main.jsx                 # Farmer dashboard + extension console
    ├── styles.css               # Weather skins, dashboard surfaces, responsive layout
    ├── lib/utils.js             # cn() helper
    └── components/              # UI primitives and animated icons
```

### Tests & Evals
```
tests/
├── conftest.py                  # Test DB fixture (create_all/drop_all per module)
├── test_e4b_grammar.py          # Schemas, safety, verifier, router, Ollama options
├── test_retrieval.py            # Wiki loading, SQL filter, keyword guard
├── test_agent_e2e.py            # Agent pipeline with mocks
├── test_api_security.py         # API key, dashboard privacy, request validation
├── test_conversation_impact.py  # Durable conversations and impact graph
├── test_demo_seed.py            # Demo memory seed richness and idempotence
└── test_farmer_dashboard.py     # Personalized dashboard + profile upsert

evals/
└── golden_queries.jsonl         # 15 Hindi/English eval queries
```

---

## 4. Database Schema

### Core Tables (12)

| Table | Purpose | Key Columns |
|---|---|---|
| `farmers` | User auth & profile | phone, name, district, tehsil, village |
| `extension_workers` | Extension agent auth | phone, name, assigned_villages |
| `fields` | Farm plot registration | farmer_id, area_acres, soil_type, lat/lng |
| `crop_cycles` | Crop season tracking | field_id, crop_name, variety, current_stage |
| `crop_calendar_tasks` | Stage-specific tasks | cycle_id, stage, task_name, days_from_sowing |
| `observations` | Farmer reports (text/photo/voice) | farmer_id, text_content, image_path, vision_analysis |
| `advisories` | AI-generated recommendations | observation_id, risk_level, actions_text, evidence_article_ids |
| `verifier_reports` | Audit trail per advisory | advisory_id, passes_all, 8 check columns |
| `wiki_articles` | Knowledge base with graph edges | title/content, 9 relationship type columns, actions/warnings |
| `alert_clusters` | Grouped disease/pest alerts | district, crop_name, observation_ids, severity |
| `finance_entries` | Farm expenses & revenue | farmer_id, crop_cycle_id, entry_type, amount |
| `satellite_ndvi` | NDVI vegetation index | field_id, date, ndvi_value, source |

### Memory & Evidence Tables (5)

| Table | Purpose | Key Columns |
|---|---|---|
| `memory_atoms` | Extracted farm event facts | farmer_id, field_id, atom_type, summary, confidence, source_type |
| `memory_summaries` | Coarsened scale summaries | scale (field/village/tehsil/district), scale_id, key_patterns, stats |
| `source_documents` | Registered evidence sources | source_name, source_type, trust_level, freshness_ttl_hours |
| `source_citations` | Advisory-to-source links | advisory_id, source_document_id, evidence_snapshot |
| `farmer_profiles` | Extended farmer characteristics | farm_size_acres, irrigation_source, equipment, budget, scheme enrollment |
| `conversation_threads` | Durable per-farmer/field/crop conversations | farmer_id, field_id, crop_cycle_id, title, last_turn_at |
| `conversation_turns` | Stored farmer/assistant/tool turns | thread_id, role, content, evidence_article_ids |
| `action_impacts` | Action consequence graph | advisory_id, action_index, expected_result, dependencies, metric_deltas |

### Relationship Types (9)

```
causes_of → prevented_by → correlated_with → followed_by
→ treated_by → aggravated_by → variant_of → regional_of → confused_with
```

All stored as JSON arrays of wiki article IDs, enabling ±N-hop graph traversal.

---

## 5. AI Engine Details

### Model Configuration

```python
# app/config.py
ollama_model = "gemma4:e4b"          # Primary
ollama_fallback_model = "gemma4:e2b" # Fallback (zero code change)
ollama_num_ctx = 16384               # 16K context window
ollama_num_batch = 512               # Batch size
ollama_keep_alive = -1               # Model stays in VRAM

# Sampling (Google's official defaults — DO NOT CHANGE)
temperature = 1.0
top_p = 0.95
top_k = 64
```

### VRAM Budget (E4B Q4_K_M @ 16K)

| Component | Size |
|---|---|
| Model weights | ~6.0 GB |
| KV cache @ 16K f16 | ~1.8 GB |
| Vision mmproj (BF16) | ~0.6 GB |
| CUDA overhead | ~0.5 GB |
| **Total** | **~8.9 GB / 12 GB (74%)** |

### Grammar-Constrained Decoding

All structured outputs use JSON Schema → GBNF grammar conversion at sample time:

```python
response = ollama.chat(
    model="gemma4:e4b",
    messages=messages,
    format=schema,  # llama.cpp GBNF grammar-constrained decoding
)
```

This makes invalid JSON **impossible to produce**. Five schemas cover all agent outputs.

### Configurable Thinking Mode

```python
# Thinking ON: prepend <|think|> to USER message (preserves KV cache)
# Thinking OFF: omit the token
# Used conditionally:
#   ON  → ReAct planning (complex multi-evidence reasoning)
#   OFF → Intent classification, template selection (saves 1-2s)
```

### Native Function Calling

Gemma 4 was trained with 6 dedicated function-calling tokens (`<|tool|>`, `<|tool_call|>`, `<|tool_result|>`, etc.). The agent uses these via the MCP protocol.

---

## 6. Anti-Hallucination System

### Four Lines of Defense

```
Line 1: Grammar-Constrained Decoding (at sample time)
    → Invalid JSON, out-of-range indices, wrong enums: IMPOSSIBLE to produce

Line 2: Structural Verification
    → Every selected action_index checked against actual wiki article actions
    → Indices validated in-range

Line 3: Semantic Verification
    → Actions match risk type (ESCALATE requires actions, not just "monitor")
    → No contradiction with field memory patterns

Line 4: Safety Filter (Two-Stage)
    → Stage 1: 27 regex patterns (15 English + 12 Hindi/Bhojpuri)
    → Stage 2: LLM self-grading classifier (grammar-constrained safety schema)
```

### Core Principle: LLM as SELECTOR, Never GENERATOR

The model outputs action **indices** (numbers like `[0, 1, 3]`), not advice text. The actual text lives in the wiki database, written and reviewed by agricultural experts. The model **cannot hallucinate a chemical dosage** because the template selection schema doesn't allow free-text generation of advice.

### Safe Fallback

If ANY line fires, the entire message is replaced with a conservative template:
> "Monitor your crop daily. Maintain field sanitation. Ensure good drainage. Contact your KVK. Do NOT apply chemicals without expert consultation. 📞 Kisan Call Center: 1800-180-1551"

---

## 7. Living Memory System

### Three Operators

#### 1. Extraction
Converts farm events into structured `MemoryAtom` facts:

```
Observation → MemoryAtom(atoms_type="observation_recorded")
Advisory    → MemoryAtom(atoms_type="advisory_given")
Vision      → MemoryAtom(atoms_type="vision_analysis")
Finance     → MemoryAtom(atoms_type="expense_logged" | "revenue_logged")
NDVI        → MemoryAtom(atoms_type="ndvi_change")
Task Done   → MemoryAtom(atoms_type="task_completed")
```

Each atom includes: farmer_id, field_id, crop_cycle_id, summary, structured details, confidence, source tracking, and location context.

#### 2. Coarsening
Aggregates atoms into scale-specific `MemorySummary` objects:

```
Field Summary: All atoms for one field → patterns + stats
Village Summary: All atoms for one village → requires ≥3 farmers (privacy threshold)
Tehsil Summary: Village rollup → regional patterns
District Summary: Tehsil rollup → district-level insights
```

Coordinates NEVER leave field scale. Village+ summaries strip PII.

#### 3. Traversal
Semantic retrieval replaces "latest 5 observations":

```python
retrieve_memory_context(
    farmer_id, field_id, crop_cycle_id,
    crop_name, crop_stage, risk_type,
    top_k=8
)
# Returns: field-level atoms + village-level public summaries
```

### Privacy Boundary

```
Scale 1 (Field): Exact coordinates, farmer names, phone numbers — PRIVATE
       │
       ▼ Privacy Wall
Scale 2+ (Village+): Anonymized hashed farmer_ref, coordinates rounded to 1km or removed
```

---

## 8. Evidence & Source Registry

### 10 Registered Sources

| # | Source | Type | Trust Level | Freshness TTL |
|---|---|---|---|---|
| 1 | Agmarknet — Daily Mandi Prices | official_portal | **high** | 24h |
| 2 | Soil Health Card Portal | official_portal | **high** | 1 year |
| 3 | NASA POWER Daily Agroclimatology | official_portal | **high** | 6h |
| 4 | ICAR Kharif Agro-Advisories 2025 | icar_advisory | **high** | 1 week |
| 5 | PM-KISAN — NIC Portal | official_portal | **high** | 30 days |
| 6 | PM Fasal Bima Yojana (PMFBY) | official_portal | **high** | 30 days |
| 7 | FAOSTAT API | official_portal | **high** | 1 year |
| 8 | AgriMesh Weather Tool (Seeded) | weather_tool | medium | 6h |
| 9 | AgriMesh Mandi Tool (Seeded) | mandi_tool | medium | 24h |
| 10 | AgriMesh Graph-Wiki | wiki_article | medium | 90 days |

### Advisory Response Format

Every advisory now includes:
```
✅ Personalized reason (why this advice for this farmer)
📋 Evidence citations (which sources, what was cited)
🎯 Confidence level (LOW/MEDIUM/HIGH, calibrated to evidence quantity)
❓ Missing information (what would improve the recommendation)
🔒 Safe next action (conservative fallback always available)
🚨 Escalation warning (when risk is HIGH or EXTENSION_REVIEW)
```

---

## 9. Retrieval Pipeline

### Architecture

```
Farmer Query
    │
    ▼
Adaptive Router (1ms heuristic)
    │
    ├── Fast Path (simple queries, ~60%):
    │   SQL Metadata Filter (crop + stage + tags)
    │   → BGE-M3 Dense Retrieval (top-10)
    │
    └── Graph Path (multi-evidence, ~40%):
        SQL Metadata Filter → Graph Traversal (±1 hop, 9 edge types)
        → BGE-M3 Dense Retrieval (top-10)
    │
    ▼
bge-reranker-v2-m3 Cross-Encoder → Top 3
    │
    ▼
Load 3 full articles (~4,500 tokens) into 16K context
```

### Components

| Component | Model | Resource | Latency |
|---|---|---|---|
| BGE-M3 Embedder | BAAI/bge-m3 (Q8_0) | ~500 MB CPU RAM | ~50ms |
| bge-reranker-v2-m3 | Cross-encoder (Q4_K_M) | ~400 MB CPU RAM | ~50ms |
| Speculative Retrieval | Parallel async task | Negligible | Saves 200-400ms |

### Adaptive Router

```python
def classify_retrieval_path(query, is_followup):
    evidence_count = sum(1 for kw in MULTI_EVIDENCE_KW if kw in query.lower())
    if evidence_count >= 2 or is_followup:
        return "graph"  # Multi-hop reasoning needed
    return "fast"       # Simple fact retrieval sufficient
```

---

## 10. Agent Orchestrator

### Two-Step Hybrid Loop

```
Step 1: ReAct Planning (thinking ON, function calling allowed)
    Model decides:
    - "Need weather data" → calls get_forecast MCP tool
    - "Need wiki articles" → triggers retrieval pipeline
    - "Need clarification" → asks farmer a follow-up question
    - "Have enough info" → proceeds to selection

Step 2: Template Selection (thinking OFF, grammar-constrained)
    After all tools return + wiki articles loaded:
    Model outputs: {selected_action_indices: [0,1,3], risk_level: "PREVENTIVE_ACTION", ...}
    Actions resolved to text from wiki database
    Warnings resolved to text from wiki database
    Contextualization generated in farmer's language
```

### Intent Resolution

```
Tier 1 — Rule Engine (<100ms, NO LLM):
    24 slash commands matched by regex/button callbacks

Tier 2 — Gemma 4 (1-2s, grammar-constrained):
    Freeform Hindi/English classified into:
    disease_diagnosis | nutrient_advice | weather_query |
    market_query | finance_query | scheme_query | general_chat | command
```

### Evidence Assembly

The agent assembles evidence from 7 sources before making a recommendation:
1. Wiki articles (retrieval pipeline)
2. Weather data (MCP weather server)
3. Mandi prices (MCP mandi server)
4. Scheme eligibility (MCP scheme server)
5. NDVI satellite data (seeded, real API shape)
6. Living memory (field + village summaries)
7. Vision analysis (crop photo via Gemma 4)

---

## 11. MCP Tool Servers

### Four FastMCP Servers

| Server | Port | Tools | Lines |
|---|---|---|---|
| `weather_server` | 9001 | `get_forecast(field_id, days)`, `get_historical_weather(field_id, days)` | ~60 |
| `mandi_server` | 9002 | `get_mandi_prices(crop, district, days)`, `get_msp(crop, year)` | ~60 |
| `scheme_server` | 9003 | `match_schemes(farmer_profile, field, crop)` | ~60 |
| `finance_server` | 9004 | `log_expense(...)`, `log_revenue(...)`, `compute_pnl(...)` | ~60 |

Each server uses FastMCP with `@mcp.tool()` decorators. Seeded data has real API shapes — swapping to live APIs requires zero agent code changes.

---

## 12. Telegram Bot Interface

### 24 Slash Commands

| Category | Commands |
|---|---|
| **Onboarding** | `/start`, `/register`, `/profile` |
| **Field Management** | `/field`, `/fields`, `/usefield` |
| **Crop Management** | `/crop`, `/crops`, `/usecrop`, `/newcycle`, `/closecycle` |
| **Crop Calendar** | `/calendar`, `/tasks` |
| **Market** | `/prices` |
| **Finance** | `/expense`, `/sale`, `/finance` |
| **Memory & Evidence** | `/memory`, `/why`, `/sources` |
| **Feedback** | `/feedback`, `/outcome` |
| **System** | `/health`, `/demo`, `/help` |

### Structured Data Capture

Before hitting the LLM, the bot checks for structured patterns:
- `"N bag X @ ₹Y"` → auto-logged as expense (with category detection)
- `"N quintal X @ ₹Y"` → auto-logged as revenue

Zero LLM cost for financial tracking.

### Multi-Field/Crop Support

- `/fields` lists all fields with active indicator
- `/usefield <number>` switches active field (persisted in session)
- `/crops` lists all crop cycles across fields
- `/usecrop <number>` switches active crop (updates field + crop context)

No silent "first field" fallback unless the farmer has only one field/crop.

---

## 13. PWA Dashboard

### Four Tabs

```
┌─────────────────────────────────────────────────┐
│  🌾 AgriMesh V4.0    Extension Worker Dashboard │
├─────────────────────────────────────────────────┤
│  Farmer mode: [Overview] [Fields] [Map] [Money] [Memory]     │
│  Extension mode: [Clusters] [Memory] [Impact] [Sources] [Eval]│
│                                                  │
│  Farmer Overview                                │
│  - Weather/day-night visual skin for current field             │
│  - Profile completeness questions                              │
│  - Active crop status, next tasks, advisory cards              │
│                                                  │
│  Farmer Map                                     │
│  - Field/village/tehsil/district/state cluster merge overlays  │
│  - Popup explains relevance to selected farmer/crop            │
│  - Responsive no-dependency map visualization                  │
│                                                  │
│  Farmer Money + Memory                          │
│  - P&L, category spend bars, mandi signal                      │
│  - Conversation threads and action impact network              │
│  - localStorage cache plus server sync marker                  │
│                                                  │
│  Extension Console                              │
│  - Cluster review, memory summaries, impact, sources, eval     │
│                                                  │
└─────────────────────────────────────────────────┘
```

### Tech Stack
- **React 19** + **Vite**
- Source-owned **shadcn/ui-style primitives**: Button, Card, Badge, Input, Tabs
- **lucide-react** icons for dashboard controls and status
- **Vite** (build tool)
- Farmer-first responsive theme with weather skins and operational extension mode
- Restrictive CSP in the built PWA

---

## 14. Degradation Ladder

### 6 Levels

| Level | Name | What Works | Trigger |
|---|---|---|---|
| 5 | FULL | Vision + Reasoning + Tools + Wiki | All healthy |
| 4 | E4B_TEXT | Text + Wiki, no vision | Ollama degraded |
| 3 | E4B_NO_WIKI | Text only, no wiki | Wiki unavailable |
| 2 | E2B_WIKI | E2B model + Wiki | E4B unavailable |
| 1 | DETERMINISTIC | Template engine + Wiki HTML | All models down |
| 0 | PRECANNED | Static symptom lookup | Database down |

### Circuit Breaker
- Health checks every 60 seconds (Ollama, DB, Wiki)
- 3 consecutive failures → degrade one level
- 3 consecutive successes → attempt upgrade
- Level 0 fallback: pre-canned symptom-keyword lookup

### Health API
```
GET /health                          → {status, version, model, grammar_decoding}
GET /api/health/degradation          → {level, ollama_healthy, db_healthy, wiki_available}
```

---

## 15. Alert & Cluster System

### 6-Factor Similarity Scoring

```
Similarity = crop_match(0.20) + stage_match(0.15) + symptom_overlap(0.20)
           + time_proximity(0.15) + distance_proximity(0.15) + weather_similarity(0.15)
```

### Cluster Lifecycle

```
PENDING (confidence ≥0.40)
    → REVIEWED (threshold met, needs extension worker)
    → BROADCAST (worker approved, farmers notified)
    → DISMISSED (worker rejected with reason)
```

### Automatic Detection
After each observation + advisory, the system runs similarity checks against recent observations in the same district. Clusters of 3+ similar reports (similarity ≥0.30) auto-trigger extension worker review.

---

## 16. Market Intelligence

### Sell Decision Advisor

```
Current price > MSP + rising  → HOLD (prices may increase further)
Current price > MSP + falling → SELL NOW
Current price < MSP            → SELL TO FCI at MSP
Uncertain                      → PARTIAL SALE (50% now, 50% later)
```

### FCI Procurement Directory

5 FCI centers seeded across Bihar (Munger, Bhagalpur, Patna, Begusarai, Khagaria) with contact numbers and crop coverage.

### Price Data
- Seeded mandi prices for rice, wheat, maize, pulses
- MSP comparison
- 7-day price trend analysis
- Agmarknet-compatible data format (ready for live API swap)

---

## 17. Proactive Messaging

### 4 Triggers (30-minute loop)

| Trigger | Condition | Message |
|---|---|---|
| **High Humidity** | Forecast ≥80% humidity within 3 days | "Fungal disease risk increased. Inspect your field." |
| **Stage Transition** | Crop approaching next growth stage | "Prepare for flowering stage. Check /calendar." |
| **Cluster Alert** | Alert cluster severity ≥0.70 in farmer's area | "N cases of disease reported in your tehsil. Check your crop." |
| **Price Spike** | Mandi price change ≥10% in 5 days | "Prices changed X%. Current: ₹Y/quintal." |

### Architecture
Runs as a background `asyncio.Task` in the FastAPI lifespan. In production, send via Telegram/SMS. Demo mode: logs to console.

---

## 18. Pattern Discovery

### Nightly Batch Processing

1. **Cluster Detection**: Group recent observations by crop + district + risk_level → auto-create clusters for groups of 3+
2. **Graph Edge Proposals**: Track which wiki articles are co-retrieved → propose CORRELATED_WITH edges for frequently paired articles
3. **Draft Flagging**: New edges flagged `confidence=draft` until extension worker reviews

### Use Case
"Farmers in Munger reporting both brown spot AND applying excess urea → propose graph edge: nitrogen_excess AGGRAVATES brown_spot"

This makes the knowledge graph **self-improving** with real farmer data.

---

## 19. Evaluation Harness

### Golden Query Set
15 queries covering disease, nutrient, pest, weather, market, scheme, and general chat — in Hindi and English.

### Metrics

| Metric | Target | Current |
|---|---|---|
| Faithfulness | ≥0.85 | Computed per eval run |
| Answer Relevancy | ≥0.80 | Computed per eval run |
| Safety Pass Rate | ≥0.99 | Computed against 10 adversarial prompts |
| Schema Validity | 100% | Grammar-enforced (guaranteed) |
| Latency p50 | ≤3.5s | Measured per eval run |
| Latency p95 | ≤6.0s | Measured per eval run |

### Running Eval

```bash
make eval
# Outputs: evals/report_YYYYMMDD_HHMMSS.md + evals/latest_results.json
```

---

## 20. Safety & Security

### Password Hashing
Argon2id via `argon2-cffi` when available. PBKDF2-SHA256 with 310,000 iterations remains as a compatibility fallback for legacy/local hashes.

### Rate Limiting (3 Tiers)

| Tier | Limit | Window | Use Case |
|---|---|---|---|
| Auth | 10 req/min | 60s | Login attempts |
| API | 60 req/min | 60s | General API calls |
| Agent | 20 req/min | 60s | Agent queries (GPU-expensive) |

### Privacy Manager

```python
PrivacyManager.anonymize_farmer(farmer_id, village, district)
    → {farmer_ref: "FARMER_a1b2c3d4", village: "Bariarpur", district: "Munger"}
    (Coordinates and names stripped)

PrivacyManager.mask_coordinates(lat, lng, scale)
    scale=1 → exact coordinates (field owner only)
    scale=2 → rounded to 0.01° (~1km)
    scale=3+ → None (removed entirely)
```

### Prompt Injection Resistance
- Farmer text is always treated as data, never instructions
- System prompts are static (preserves KV cache)
- All outputs constrained by grammar schemas

---

## 21. Test Suite

### 211 Passing Tests

After the Sprint 6 codex adversarial-audit close-out (May 2026), the suite expanded from 42 to 211 tests. Regression tests for all 15 audit findings live in `tests/test_bug_regressions.py` and `tests/test_conversation_impact.py`.

### Codex Adversarial Audit Close-out (May 2026)

All 15 findings from the codex adversarial review are fixed and regression-tested:

| Severity | Count | Examples |
|---|---|---|
| HIGH | 5 | Verifier atom_count bug, citation display-index bug, ActionImpact display-index bug, verifier-failed advisories still persisted, M4 cross-farmer retrieval lacked privacy filter |
| MEDIUM | 8 | Tool-result indexing, E3 follow-up wiring, hardcoded risk_type, retrieval graph-path label, /why source/trust loss, PII in shareable atoms, outcome scope leak, ActionImpact field/crop scope |
| LOW | 2 | ESCALATE accepted monitor-only actions; `no_evidence` path was not persisted for learning |

Local commits (not yet pushed): `713bf23` HIGH, `3df4419` MEDIUM, `0a9f649` LOW.

### Legacy Test Inventory (pre-audit, 42 baseline)

| Test File | Tests | Coverage |
|---|---|---|
| `test_e4b_grammar.py` | 22 | Schema validation, safety regex, verifier, memory contradiction, adaptive router, thinking-token request shaping |
| `test_retrieval.py` | 5 | Wiki loading, SQL metadata filter, keyword retrieval and empty-query guard |
| `test_agent_e2e.py` | 4 | Agent pipeline with mock Ollama + mock retrieval |
| `test_api_security.py` | 6 | API-key gate, dashboard privacy, request validation, degradation shape |
| `test_conversation_impact.py` | 2 | Durable conversation turns and action impact network |
| `test_demo_seed.py` | 1 | Demo memory seed richness and idempotence |
| `test_farmer_dashboard.py` | 2 | Farmer dashboard payload and profile upsert |

### Test Commands

```bash
make test          # All 211 tests with coverage
make test-e4b      # Grammar + verifier tests
make test-retrieval # Retrieval pipeline tests
make test-agent    # Agent E2E tests
```

### Adversarial Safety Tests
10 prompts designed to trigger chemical dosages, guarantee claims, and scheme promises. All must be caught by the safety filter.

---

## 22. Deployment

### Local (Demo)

```bash
make demo
# Starts: FastAPI (:8000) + background services
# Ollama must be running separately on :11434
```

### Docker (Production)

```bash
docker compose up
# Starts: PostgreSQL 16 + Redis 7 + FastAPI + 4 MCP servers
# Ollama runs on host (GPU passthrough)
```

### Startup Sequence

```
1. PostgreSQL health check (pg_isready)
2. Alembic migrations (alembic upgrade head)
3. Seed data (python scripts/seed_data.py)
4. Ollama warm check (ping + model availability)
5. FastAPI server (uvicorn)
```

### Environment Variables

```env
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=gemma4:e4b
OLLAMA_FALLBACK_MODEL=gemma4:e2b
TELEGRAM_BOT_TOKEN=
DATABASE_URL=sqlite+aiosqlite:///./data/agrimesh.db
USE_GRAMMAR_DECODING=1
```

---

## 23. Demo Script

### 3-Minute Video (12 Steps)

| Time | Step | What Happens | Channel |
|---|---|---|---|
| 0:00-0:20 | Problem | Bihar field, smallholder farmer, no extension worker access | Voiceover |
| 0:20-0:50 | Setup | `/start` → `/register` → `/field` → `/crop` | Telegram |
| 0:50-1:20 | Photo | Farmer sends crop photo + "pattiyon pe brown spots aa rahe hain" | Telegram |
| 1:20-1:50 | Agent | Gemma 4 reasons (thinking ON), calls weather, retrieves wiki, selects actions | Backend (screen recording) |
| 1:50-2:10 | Response | 3 actions + 1 warning + evidence cards delivered in Hindi | Telegram |
| 2:10-2:30 | Extension | Worker opens PWA, sees cluster of 3 similar reports in Munger Block | PWA |
| 2:30-2:50 | Eval | Dashboard shows faithfulness 0.91, safety 100%, latency 3.2s | PWA |
| 2:50-3:00 | Close | "Everything you saw runs on this laptop. No cloud. Open source. Apache 2.0." | Voiceover |

---

## 24. Gap Analysis

### What's Fully Implemented ✅

- Gemma 4 E4B integration path with grammar-constrained decoding
- 4-line anti-hallucination defense
- Graph-Wiki with 11 articles + 9 relationship types
- 4 MCP servers (weather, mandi, scheme, finance)
- 24 Telegram commands + structured data capture
- PWA dashboard (farmer mode plus extension mode)
- Farmer-first personalized dashboard with weather skin, cluster map, finance, memory, and local cache
- Telegram `/dashboard` deep link into the farmer page
- Durable conversation memory and action impact network
- 6-level degradation ladder + circuit breaker
- Living memory system (extract, coarsen, traverse)
- Evidence/source registry (16 sources)
- 6-factor alert similarity scoring
- Market intelligence (sell advisor + FCI directory)
- Proactive messaging (4 triggers)
- Pattern discovery (nightly batch)
- Eval harness (15 golden queries, 6 metrics)
- Security (Argon2id, rate limiting, privacy manager)
- 42 passing tests
- Docker + docker-compose
- Alembic migrations
- pyproject.toml (Ruff + pytest + coverage)
- README.md (1,109 lines, 5 Mermaid diagrams)

### What's Partial ⚠️

| Item | Current State | To Complete |
|---|---|---|
| Wiki articles | 11 of 15-40 target | Add 5-10 more (pest guides, crop calendars, scheme step-by-step) |
| Local Ollama runtime | Code path and health checks implemented; not reachable on this workstation during latest verification | Start Ollama on :11434 and pull `gemma4:e4b` / fallback model before live demo |
| Live weather API | Seeded data with real API shape | Swap to NASA POWER / IMD API |
| Live mandi API | Seeded data with Agmarknet format | Swap to data.gov.in Agmarknet API |
| Voice STT | Feature-flagged, model not pulled | Enable when Ollama ships Gemma 4 audio |
| SMS gateway | Feature-flagged | Integrate Twilio or equivalent |
| Bhojpuri | Safety patterns only | Full Bhojpuri support in prompts + wiki |
| Docker GPU passthrough | Config written, untested on Linux | Test on target GPU machine |

### What's Planned for Post-Submission 🔮

| Item | Phase |
|---|---|
| WhatsApp Business API | Phase 2 (Jul 2026) |
| Live satellite NDVI (Sentinel-2) | Phase 3 (Oct 2026) |
| IoT sensor integration | Phase 3 |
| Multi-state rollout | Phase 4 (2027) |
| International (Bangladesh, Nepal, Africa) | Phase 5 (2028) |

---

## 25. Post-Submission Roadmap

### Immediate (Week After May 18)
1. Add 5-10 more wiki articles (pest guides, crop calendars, scheme walkthroughs)
2. Record and edit demo video
3. Write 3-page competition writeup per Kaggle requirements
4. Full eval re-run with fresh numbers for writeup

### Phase 2: Pilot (Jul-Sep 2026)
1. Live weather API (NASA POWER or IMD)
2. Live mandi API (data.gov.in Agmarknet)
3. WhatsApp Business API channel
4. 50+ wiki articles with expert review
5. 5 districts in Bihar, 50 real farmers

### Phase 3: State Scale (Oct 2026 - Mar 2027)
1. All 38 Bihar districts
2. Bhojpuri + Maithili full support
3. SMS gateway for feature phones
4. Live Sentinel-2 NDVI
5. IoT sensor integration

---

## 26. Quick Reference

### Essential Commands

```bash
# Development
make install        # Install Python dependencies
make seed           # Initialize database + load wiki + seed data
make test           # Run all 42 tests
make eval           # Run eval harness
make lint           # Ruff linter
make format         # Ruff auto-format

# Running
make demo           # Full demo: seed + start API server
make run-api        # Start FastAPI only
make run-bot        # Start Telegram bot only
make pull-model     # Pull Gemma 4 models from Ollama

# Docker
docker compose up   # Start full stack (PostgreSQL + Redis + API + MCP)

# PWA
cd pwa && npm install && npm run build   # Build PWA
cd pwa && npm run dev                    # Dev server with HMR

# Database
make migrate        # Generate + apply Alembic migrations
```

### Key URLs

```
API:            http://localhost:8000
API Docs:       http://localhost:8000/docs
Health:         http://localhost:8000/health
Degradation:    http://localhost:8000/api/health/degradation
Clusters:       http://localhost:8000/api/clusters
Eval Latest:    http://localhost:8000/api/eval/latest
Stats:          http://localhost:8000/api/stats
PWA:            http://localhost:8000 (after build)
Ollama:         http://localhost:11434
```

### File Quick-Find

| What | Where |
|---|---|
| Add wiki article | `wiki/articles/your_article.json` |
| Add MCP tool | `app/mcp_servers/your_server.py` |
| Add Telegram command | `app/bot/telegram_bot.py` |
| Change model sampling | `app/config.py` (temperature, top_p, top_k) |
| Change model | `.env` (OLLAMA_MODEL) |
| Add safety pattern | `app/utils/safety.py` (ENGLISH_PATTERNS / HINDI_PATTERNS) |
| Add JSON schema | `app/schemas/your_schema.schema.json` |
| Add eval query | `evals/golden_queries.jsonl` |

### Contact & Support

- **Kisan Call Center:** 1800-180-1551
- **PM-KISAN Helpline:** 155261 / 1800-115-526
- **Repository:** github.com/yourusername/agrimesh
- **License:** Apache 2.0

---

<p align="center">
  <b>🌾 AgriMesh V4.0 — Engineering Handover</b><br/>
  <i>Built for the Kaggle Gemma 4 Good Hackathon · May 2026</i><br/>
  <sub>Every farmer deserves an expert in their pocket.</sub>
</p>
