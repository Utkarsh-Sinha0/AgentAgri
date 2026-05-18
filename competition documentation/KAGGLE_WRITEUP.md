# 🌾 AgriMesh: AI Agricultural Intelligence for 500 Million Smallholder Farmers

## Gemma 4 · Anti-Hallucination Architecture · Living Memory · Zero-Cost-at-Scale

---

## The Problem

A farmer in Munger sees brown spots on his rice. The nearest extension worker serves 5,000 others. By the time advice arrives, he loses 30% of his yield — not because the solution doesn't exist, but because it never reached him.

**500M smallholder farmers** lose **$50B+ annually** to preventable diseases, pests, and poor decisions. They sell 20-40% below MSP. They miss scheme deadlines. They apply wrong pesticides. AgriMesh puts a **Gemma 4 expert in every farmer's pocket** — via Telegram, in Hindi, at zero per-query cost.

---

## Hardware Reality → Architecture That Scales

We built and demoed this entirely on a **single RTX 3060 laptop with 6 GB VRAM**. If it runs on a student's laptop, it will fly on a proper GPU server.

### Demo Stack (Today)

| Component | Model | Hardware |
|-----------|-------|----------|
| **Primary LLM** | Gemma 4 E4B Q4_K_M (~4.5 GB) | RTX 3060 6GB |
| **Fallback LLM** | Gemma 4 E2B Q4_K_M (~2.5 GB) | Same GPU |
| **Embeddings** | BGE-M3 | CPU only |

### Production Vision

The architecture is **model-agnostic**. Change one env var (`OLLAMA_MODEL`) and AgriMesh runs any Gemma 4 variant — from today's E4B to the full **Gemma 4 26B or 31B** on a dedicated server. The pipeline, grammars, verifier, and memory don't change. Only the model size.

**Endgame**: One GPU server on bare metal or private cloud serves every farmer in a region via Telegram. Cost per farmer → **zero** — no API fees, just hardware and open-source software already built. The database becomes the single source of truth for every field: crop history, disease patterns, soil data, NDVI trends, pesticide usage, market prices, scheme eligibility. Every interaction enriches a permanent field record. Alerts fire automatically before outbreaks become epidemics.

---

## Stack Under Constraint

Every choice was dictated by **6 GB VRAM** — but each turned out to be the objectively right decision independent of hardware.

### Vision: Native Gemma 4 Multimodal

A separate vision model needs extra VRAM we don't have. Cloud APIs cost ₹5Cr/day at scale. **Gemma 4 E4B's multimodal** shares weights already loaded — zero extra VRAM, zero API cost, zero data exfiltration. One model for vision and reasoning = one fallback path.

### Embeddings on CPU

BGE-M3 on CPU (~500 MB RAM). GPU is saturated at 4.5 GB, but CPU embedding is better: it never contends with LLM token generation for GPU cycles.

### Grammar-Constrained, Not Free-Text

Smaller models hallucinate more. JSON schemas make hallucination **structurally impossible** at sample time. Mandatory at 4.5B params; a 70B model might not need it, but should have it anyway.

### RAG, Not Fine-Tuning

Fine-tuning needs 12-24 GB VRAM. RAG with 11 Graph-Wiki articles is superior: add a JSON file for any new crop, pest, or scheme — no retraining, no model drift, expert-reviewable knowledge.

### Ollama, Not Cloud APIs

At 500M farmers, cloud inference is economically impossible. Local inference via Ollama = zero per-query cost, zero data leaving the deployment boundary.

---

## Architecture: How We Use Gemma 4

**Never let the LLM generate advice text.** AgriMesh uses a two-step hybrid loop exploiting Gemma 4's native capabilities:

| Gemma 4 Feature | How We Use It |
|----------------|---------------|
| **Thinking Mode** (`<\|think\|>`) | ON for ReAct planning — multi-step reasoning across weather, disease, tools |
| **Native Function Calling** | ReAct planner emits tool calls; MCP servers execute weather, mandi, scheme, finance in parallel |
| **Multimodal Vision** | Crop photo analysis at ~280 tokens — shared LLM weights, no separate model |
| **Grammar-Constrained Decoding** | 5 JSON schemas via GBNF — model outputs only integer indices, never free-text |
| **Multilingual** | Hindi/Hinglish/English intent classification; response in farmer's language |

**Step 1 — ReAct Planning (thinking ON, function calling ON):** Gemma 4 reasons fully, emits tool calls, and requests wiki retrieval.

**Step 2 — Template Selection (thinking OFF, grammar-constrained JSON):** Gemma 4 outputs only integer indices — `[0, 1, 3]` — that map to expert-written actions in a Graph-Wiki database with 11 articles connected by 9 relationship types. It **cannot generate advice text**. Grammar-constrained decoding makes invalid JSON structurally impossible at sample time.

### 9-Step Pipeline

```
Farmer sends photo + text
  → Step 0: Gemma 4 Vision analyzes photo
  → Step 1: Intent classification (disease? price? scheme?)
  → Step 2: Speculative retrieval fires in parallel
  → Step 3: MCP tools called (weather, mandi, scheme, finance)
  → Step 4: BGE-M3 dense + sparse retrieval → reranker → top-3 wiki articles
  → Step 5: Universal KB + Living Memory loaded
  → Step 6: Gemma 4 selects action indices (grammar-constrained)
  → Step 7: Verifier — 4 lines of defense
  → Step 8: Advisory delivered in Hindi with evidence citations
  → Step 9: Memory atoms extracted, alert clusters updated
```

---

## The 4-Line Verifier

Every advisory passes through an auditable component:

| Line | Check | Stops |
|------|-------|-------|
| Structural | Indices resolve to real wiki actions | Out-of-range indices |
| Semantic | Actions match risk; no memory contradiction | Recommending what's already done |
| Safety | 27 regex patterns + LLM self-grading | Chemical overdoses, medical guarantees |
| Calibration | HIGH confidence ≥ 3 articles + recent evidence | Overconfidence on weak data |

The VerifierReport persists with every Advisory as an audit trail.

---

## Living Memory: M1–M4

AgriMesh learns from every interaction:

- **M1: Temporal Decay** — Atoms decay with half-lives calibrated to the monsoon cycle (disease: 30d, market: 7d, outcomes: 90d). Old evidence doesn't crowd out new.
- **M2: Outcome-Weighted Confidence** — `/outcome improved 5` boosts confidence of every atom in the causal chain by +0.10. Worsened outcomes subtract.
- **M3: Causal Chains** — One observation → vision → advisory → outcome forms a traversable causal graph.
- **M4: Cross-Farmer Learning (k-Anonymity)** — Atoms from other farmers surface only when ≥3 distinct farmers contribute.

Every insight flows upward through privacy gates: **Field** → **Village** (k=3) → **Tehsil** → **District** → **State** → **National**. The same data that helps one farmer builds regional outbreak maps for policymakers.

---

## Alert Clusters: Outbreak Prevention

When ≥3 farmers in the same district report similar symptoms, AgriMesh scores similarity across 6 factors (crop × stage × symptom × time × distance × weather) and creates an AlertCluster:

```
Observation → Similarity Check → Cluster (≥3, >0.30)
  → INTERNAL_WATCH → FARMER_WATCH → EXTENSION_REVIEW → BROADCAST
```

An extension worker sees: "3 farmers reporting brown spot in Bariarpur block." Reviews and broadcasts a warning to all rice farmers. **Outbreak prevention at the village level.**

---

## Degradation Ladder

| Level | What Works |
|-------|-----------|
| L5 — FULL | Vision + Reasoning + Tools + Wiki |
| L4 — E4B_TEXT | Text + Wiki, no vision |
| L3 — E4B_NO_WIKI | Text only |
| L2 — E2B_WIKI | E2B model + Wiki |
| L1 — DETERMINISTIC | Template engine |
| L0 — PRECANNED | Static lookup |

Health-checks every 60s. **The farmer always gets an answer.**

---

## Economic Impact

| Lever | Per Farmer | At 100K Farmers |
|-------|-----------|----------------|
| Disease prevention (20-30% yield saved) | ₹16,000-24,000/season | ₹160Cr+ |
| MSP-aware sell decisions | ₹21,000/sale avoided loss | ₹210Cr+ |
| Scheme enrollment (PM-KISAN, PMFBY, KCC) | ₹6,000/yr + insurance | ₹60Cr+ |

---

## Technical Rigor: 211 tests · 100% safety · 100% schema validity · p50 ≤3.5s · 15/15 audits fixed.

---

## The Demo (3 Minutes)

| Time | What happens |
|------|-------------|
| 0:00 | Munger farmer. 1.2 hectares. No extension worker. |
| 0:20 | `/demo` binds seeded farm with disease history, soil, NDVI, finance, cluster. |
| 0:40 | `/memory` shows past diseases, soil tests, NDVI trend. `/prices` compares mandi + MSP. `/finance` shows P&L. |
| 1:25 | Farmer sends photo + "pattiyon pe brown spots hain". |
| 1:55 | Gemma 4 Vision analyzes. Agent calls weather (76% humidity), retrieves wiki + memory, picks indices. |
| 2:20 | Hindi advisory: drain field, remove leaves, monitor 3-5 days. Evidence citation. |
| 2:40 | Extension worker sees cluster: 3 farmers in Bariarpur. Broadcasts alert. |
| 2:52 | Readiness check: local model, no cloud, 211 tests passing. |

---

## Tracks

| Track | Connection |
|-------|-----------|
| **Global Resilience** (Primary) | Disease detection → crop loss reduction (SDG 2). MSP-aware selling → income improvement (SDG 1). Weather + outbreak alerts → climate adaptation (SDG 13). |
| **Ollama Special Tech** | Gemma 4 E4B on Ollama + consumer GPU. One env var swaps to any Gemma variant. |
| **Digital Equity** | Hindi-first, basic smartphone, Telegram — no app store, low bandwidth, voice-ready. |
| **Safety & Trust** | 4-line verifier, grammar-constrained decoding, k-anonymity, auditable reports. |

---

**🌾 Every farmer deserves an expert in their pocket.**
