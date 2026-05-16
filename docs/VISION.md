# AgentAgri Vision — the agent for every farmer, everywhere

This is the product roadmap, written for the farmer first, the operator second. What AgentAgri does today, what ships next, and where we want to be in two years.

For the current capability list see [FEATURES.md](FEATURES.md). For implementation status see the project memory `project_phase2_resume.md`.

---

## North star

**One agent, one chat thread, every question a farmer has.**

A 9th-pass farmer in Munger should be able to send a WhatsApp voice note in Bhojpuri, get back a Hindi voice reply that cites the right wiki page and her last month's outcomes, and — if she's confused — be asked a clarifying question instead of given a wrong answer. The same agent should work for a tomato grower in Maharashtra, a wheat farmer in Punjab, a smallholder coffee planter in Karnataka, and (eventually) a farmer in Kenya or Brazil. The advisory layer is universal; the data layer is local.

We measure success by three things, in this order:
1. **Did the advice work?** Tracked via `/outcome`. Currently >70% "worked" or "partial" on internal eval.
2. **Did she come back?** 7-day return rate. The agent only matters if it earns the next message.
3. **Did she trust it?** Average `/feedback` rating + qualitative comment review.

---

## Today (shipped, on `origin/main`)

What a farmer can do right now:

- Send a text question in Hindi or English on Telegram.
- Send a photo of a sick leaf and get a disease guess + treatment grounded in our wiki.
- Send a voice note (flag-gated — see VOICE below) and have it transcribed.
- Get a 4-line advisory that cites sources, names confidence, and changes wording when the recommendation changes vs. last reply.
- Log feedback (1–5 stars) and outcome (worked / partial / failed) — the agent learns and reranks future memory recall.
- Open a web dashboard with crop calendar, finance rollup, scheme matches, recent advisories.
- Chat across multiple parallel topics — the bot routes each message to the right conversation thread.

Under the hood: Gemma 4 E4B + BGE-M3 retrieval + 4 MCP tool servers + Alembic-versioned schema + 216 passing tests.

---

## Immediate next (1–3 months)

### 1. Voice loop — STT + TTS, lightweight + open-source

The single highest-leverage UX upgrade. Munger pilot farmers have low text literacy; voice is how the agent reaches them at scale.

**STT (Speech → Text)**

| Candidate | Size | Languages | Why | Trade-off |
|-----------|------|-----------|-----|-----------|
| **`faster-whisper` tiny/base** | 75 MB / 145 MB | 99 incl. hi, mr, bn | CTranslate2 backend, ~4× real-time on CPU, Apache-2.0 | tiny is rough on Indic — base is the practical floor |
| **`distil-whisper-large-v3-hi`** | 750 MB | hi primary | Distilled, ~6× faster than full Whisper-large, strong on Hindi | Hindi-only build; need a second model for English-mixed |
| **AI4Bharat IndicWav2Vec2** | 350 MB | 9 Indic incl. bho | Best WER on Bhojpuri and rural Hindi, free | Less mature ONNX path; ships as Fairseq |
| **AI4Bharat IndicConformer** | 600 MB | 22 Indic | Production-grade, used by govt apps | Larger, slower than Whisper-tiny |

Decision (planned): `faster-whisper base` as default, `IndicConformer` when `ENABLE_BHOJPURI=true` or when the user's profile region is rural Bihar/Jharkhand/UP/MP. Routed in `app/services/voice.py` based on language detect from the first second of audio.

**TTS (Text → Speech)**

| Candidate | Size | Languages | Why | Trade-off |
|-----------|------|-----------|-----|-----------|
| **AI4Bharat Indic-Parler-TTS** | 880 MB | 21 Indic | Best open Hindi prosody, MIT, controllable voice description prompt | Slow on CPU (~2× real-time) |
| **Coqui XTTS v2** | 1.8 GB | 17 incl. hi, en | Voice cloning, Coqui-license (non-commercial) | License blocks commercial deployment |
| **Piper TTS** | 60–120 MB | 30+ | Tiny, ONNX, MIT, real-time on Raspberry Pi | Hindi voice quality is robotic, English fine |
| **MeloTTS** | 220 MB | hi, en, others | Fast, decent quality, MIT | Limited prosody control |

Decision (planned): **Indic-Parler-TTS** for Hindi/Bhojpuri (production), **Piper** as the emergency fallback for low-RAM hosts, **MeloTTS** for English. Selected by language + host RAM at runtime.

**Latency budget for voice round-trip:** target 4s from "farmer stops speaking" to "TTS audio sent." Breakdown: STT 1.0s + agent pipeline 2.0s + TTS 1.0s. Achievable on a 16 GB GPU host; CPU-only farmers get text reply with a "voice coming" indicator.

### 2. Hinglish input — code-mixed Hindi/English in Latin script

Most rural smartphone users type Hindi in Latin script (`tamatar me yellow patch ho raha`). The pipeline already detects language via `langdetect`, but mixed-script defeats it.

Plan:
- Add a fast pre-classifier (Gemma-prompted) that flags Hinglish before normal language detect.
- Pass Hinglish unchanged into the agent — Gemma 4 handles it natively, no transliteration round-trip needed.
- Maintain a small bilingual lexicon in `app/services/i18n/hinglish.py` for tokens the embedder mis-handles (e.g. "tikka" → leaf blight, "lapeti" → wilt).
- Wiki retrieval gets a second pass with the romanized → Devanagari conversion to catch articles indexed only in Hindi.

No new model required — Gemma 4 understands Hinglish out of the box.

### 3. Dashboard with infographics

Today's dashboard is functional. Next step: visual at a glance.

- **Crop calendar Gantt** — sowing → harvest, with task markers and weather overlay.
- **Finance donut + 30-day cashflow line** — expense breakdown by category, sales by buyer, ROI per cycle.
- **Memory map** — "what the agent knows about you" displayed as a graph (atoms → patterns → outcomes). Builds trust; farmer can correct wrong memories.
- **Outcome heatmap** — by crop × month × advice category. Helps the farmer see her own track record.
- **NDVI tile** — current field greenness from Sentinel-2 (already in evidence bundle; needs UI).

Stack: Preact + TailwindCSS + Chart.js, served as a static PWA against the FastAPI dashboard API.

### 4. Gemini vision for disease ID — multi-model

Today the agent uses Gemma 4 with vision for photo analysis. Gemma is good; Gemini Vision (or Google's open Paligemma) is better at fine-grained disease ID where leaf texture matters.

Plan:
- Add an env flag `VISION_MODEL=gemma|gemini|paligemma`.
- When set to `gemini`, the photo is sent to Gemini Pro Vision (with farmer consent in onboarding).
- Default stays on-device (Gemma); Gemini is opt-in for hard cases (low-confidence Gemma response → escalate prompt: "want a second opinion?").
- Cache disease verdicts by image hash so the same photo doesn't re-bill.

Privacy bar: photo + crop name only — no farmer identity, no location more granular than district.

### 5. Clarifying-question loop

The agent currently has a 3-branch routing (continue / new / ambiguous) for thread placement. We extend the same idea to **content**: when the verifier flags low-confidence on a recommendation, instead of hedging, the agent asks one clarifying question.

Examples:
- "Yellow spots — are they only on lower leaves, or also at the top?" → answer disambiguates magnesium deficiency vs. early blight.
- "When you say 'water is over' — the canal stopped, or the tank is empty?" → routes to scheme vs. micro-irrigation advice.
- "Tomato — round red, or oval Roma?" → variety-specific spray dosage.

Implemented as a 4th branch in `route_to_thread` + a new `clarification` template that the verifier prefers when evidence strength is `low`. Persisted as a `clarification_request` atom so the answer auto-binds to the next message.

---

## 6–12 month roadmap

### 6. WhatsApp + IVR channels
- WhatsApp Business Cloud API — same agent core, swap bot module (see [SETUP.md §5.4](SETUP.md#54-replace-telegram-with-whatsapp--sms)).
- IVR via Exotel/Plivo for feature-phone farmers — voice-only loop, 30s replies.

### 7. Bhojpuri end-to-end
- AI4Bharat models already cover STT; Indic-Parler-TTS handles TTS.
- Wiki corpus needs Bhojpuri augmentation — translate the top-200 most-recalled articles.
- `ENABLE_BHOJPURI=true` flips the flag; everything else is data.

### 8. Federated outcomes — cross-farm learning at scale
- M4 (cross-farmer pooling with k=5) is shipped. Next: federated update — when 10+ farmers in a district report the same pattern, atoms propagate as a "regional alert" prior to other farmers' next query.
- Always anonymized, always opt-in, k-anonymity preserved.

### 9. Soil + sensor integration
- Cheap NPK sensors (~₹500) are now mainstream in pilot programs.
- Add an MCP server `app/mcp/sensor_server.py` that accepts farmer-submitted readings (form + photo of the meter), validates, persists, and surfaces as evidence.
- Couples nicely with the Sentinel-2 NDVI we already pull.

### 10. Mandi forecasting — not just current prices
- Today: AgMarknet snapshot of yesterday's prices.
- Plan: tiny time-series model (~10 MB) per crop × mandi predicting 7-day movement.
- Farmer asks "sell now or wait?" → agent shows current + forecast + confidence.

### 11. Scheme application assist
- Today: scheme matcher tells the farmer she's eligible.
- Plan: pre-fill the application form (PM-KISAN, KCC, crop insurance) from her stored profile, generate a printable PDF, walk her through the steps via Telegram.

### 12. Veterinary + livestock module
- Smallholders in our pilot region keep cattle, goats, poultry. Same architecture extends — different wiki corpus, different MCP tools (vaccination calendar, milk price feed), same memory & verifier.

---

## 12–24 month vision

### 13. Global expansion — the universal advisory layer

The agent is intentionally crop-agnostic and region-agnostic at the code level. To deploy in Kenya:

1. New wiki corpus (Swahili + English, Kenyan crops).
2. New MCP tools (Nairobi weather, NCPB price feed).
3. New language pair (Swahili STT/TTS — Whisper handles Swahili, MMS-TTS has it).
4. Same Gemma 4 (multilingual), same retrieval, same memory, same verifier.

Estimated effort per country: 4 dev-weeks once the playbook is written. We target 5 countries by end of 2027.

### 14. Embedded device — `Agri-Pi`
- Raspberry Pi 5 + 8 GB RAM running Ollama with `gemma4:e2b` + Piper TTS + faster-whisper-tiny.
- Sub-₹15,000 device that runs the agent offline; syncs memory + new wiki to cloud when wifi is up.
- Solves the connectivity ceiling for the deep rural last mile.

### 15. Community knowledge contributions
- Trusted farmers / KVK officers can submit corrections that become candidate wiki edits.
- Lightweight review queue, versioned, attributed.
- Agent's memory graph already has `correlated_with` edges from pattern discovery — extend with `learned_from_farmer` edges for community-sourced patterns.

### 16. Insurance + credit underwriting
- The agent has 12+ months of outcome history per farmer.
- With consent, that history is the strongest signal lenders/insurers have ever had for smallholders.
- We never sell data; we offer the farmer a one-tap "share my track record with X bank to get a loan offer."

### 17. The agent as a research lens
- Aggregated, anonymized outcome data is gold for agronomy research.
- Every quarter we publish open datasets: "yellow stem borer outcomes by intervention type, by district, n=2400." Free for universities, agtech startups, govt agencies.
- Reciprocity: research feeds back into the wiki corpus and pattern priors.

---

## Engineering principles that survive every release

1. **Local first.** Farmer data does not leave her device unless she opts in for a specific action (Gemini Vision second opinion, sharing track record with a lender).
2. **Reversible by construction.** Every change is a single commit revertible to `base-state-2026-05-16`. We never ship a feature we can't pull back in 5 minutes.
3. **Cite or hedge.** No claim without a source in the evidence bundle. If sources disagree, the agent says so.
4. **One model, swap-able.** The whole agent runs on whatever Ollama hosts. Want to try Llama 4? Change one env var.
5. **Tests before features.** New capability = new test. 216 → 500 → 1000. The eval harness runs on every PR.
6. **No farmer is too small.** The 0.4-acre subsistence grower gets the same agent as the 40-acre commercial farm.

---

## What we are not building

To stay honest: we are not building a marketplace, not building a logistics product, not building a fintech credit product, not building a social network for farmers. Those are all good ideas; they are someone else's. AgentAgri is a single thing — an advisory agent that earns trust by being right, citing its work, and remembering.

We can always be the advisor *next to* those products. We won't be them.
