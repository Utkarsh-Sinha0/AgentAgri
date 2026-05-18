# AgriMesh: Evidence-Based Agricultural Intelligence That Improves With Every Field Outcome

## Subtitle

A Gemma 4-powered agricultural agent that connects crop diagnosis, weather, market prices, schemes, finance, safety verification, and living farmer memory through Telegram and a React PWA.

## Selected Track

Impact Track: Global Resilience  
Special Technology Alignment: Ollama

## Writeup

Smallholder farmers face a decision chain, not a single question. A farmer who sees brown spots on rice leaves may need crop-health triage, weather timing, pesticide safety, nearby outbreak awareness, expected market pressure, and a memory of what was already tried. Existing tools often split these decisions across separate apps or provide generic answers without evidence. AgriMesh is built around the opposite idea: one accessible agent for the full agricultural solution loop, grounded in evidence and designed to become more useful as it records outcomes.

AgriMesh is a working proof-of-concept that uses Telegram as the farmer interface and a React PWA as the dashboard. The backend is FastAPI, with async SQLAlchemy over SQLite for development or Postgres for production. The agent pipeline lives in `app/services/agent.py`, the Telegram interface in `app/bot/telegram_bot.py`, and the Gemma 4 integration in `app/utils/ollama_client.py`. Tool servers under `app/mcp_servers/` expose weather, mandi/MSP, scheme, finance, and crop-knowledge capabilities. The result is a practical system where a farmer can send text, voice, or a crop photo and receive a confidence-calibrated answer with citations, next actions, and safety warnings.

Gemma 4 is not used as an unchecked answer generator. It is used as the reasoning layer inside a guarded agent. Through Ollama, AgriMesh runs `gemma4:e4b` as the primary local model and `gemma4:e2b` as fallback. Thinking mode is used for ReAct-style tool planning through `OllamaClient.chat(..., thinking=True)`. Function-calling behavior is implemented through `plan_tools()`, which emits schema-bound tool calls that `AgentOrchestrator._execute_tool()` dispatches to MCP services. This means weather, market, finance, and scheme facts come from tools instead of model memory.

Gemma 4 multimodal vision is used through `analyze_crop_photo()`, allowing a farmer to show a diseased leaf instead of describing symptoms precisely. Grammar-constrained decoding is used by passing JSON schemas from `app/schemas/` through Ollama's `format` field for intent classification, tool plans, safety checks, action templates, and cluster summaries. Multilingual output binding is handled by `_language_directive()`, which keeps replies aligned to English or the farmer's selected Indic-language preference, with English traceability where appropriate.

The advisory architecture has twelve major steps. First, the system hydrates farmer, field, location, and crop-cycle context. If an image exists, Gemma 4 vision creates a hypothesis. The model then classifies intent using constrained JSON and routes the conversation thread. In parallel, Graph-Wiki retrieval collects evidence while Gemma 4 plans tool calls. Retrieval combines BGE-M3 dense search, lexical boosting, reranking, previous-advisory bias, and universal knowledge sources including official reference manuals. The orchestrator then loads living memory, farmer profile, field data, crop stage, and NDVI context before asking Gemma 4 to select structured actions and warnings.

The most important engineering decision is verification before delivery. AgriMesh includes a safety and evidence verifier in `app/services/verifier.py`. It checks that selected action and warning indices resolve to actual evidence, detects contradictions with recent completed actions or outcomes, prevents HIGH confidence unless evidence richness and recency justify it, and redirects unsafe pesticide or dosage advice to safer fallback or escalation language. This matters because agricultural AI can cause real harm if it invents market prices, recommends unsafe chemical behavior, or gives false certainty during disease escalation.

AgriMesh also includes a living memory system rather than a raw transcript dump. Memory atoms use temporal decay, outcome boosting, causal chains, privacy scopes, and redaction support. If an advisory worked, its outcome can become stronger future context. If it failed or worsened, the system can downgrade similar advice and treat the issue as a possible local signal. Cross-farmer learning is gated by k-anonymity, and outbreak logic cascades reports from village to pincode, tehsil, and district with a 10% threshold and a two-reporter floor. This is the "more you use it, the better it becomes" loop: each verified observation and outcome strengthens local intelligence without exposing another farmer's raw details.

The product surface is intentionally familiar. Telegram supports registration, crop and field setup, text/photo/voice questions, `/prices`, `/finance`, `/memory`, `/why`, `/sources`, `/outcome`, `/dashboard`, and redaction through `/forgetme`. The PWA reads from the same database and shows farm overview, crop analysis, weather, market prices, AI reasoning traces, citations, history, settings, and a Gemma 4 capability proof page at `/gemma4`. Capability proof is also exposed through `GET /api/v1/capabilities` and the in-process capability log, so judges can verify that the demo reflects real model behavior.

The project targets Global Resilience because agriculture is a frontline climate and food-security system. FAO reports that small family farmers produce around one-third of the world's food, and plant pests can destroy up to 40% of global crop production annually. India's Agriculture Census 2015-16 reports that small and marginal holdings form about 86% of operational holdings. In that context, timely, low-cost, local, evidence-based advice has direct resilience value.

The main challenges were grounding, safety, and usefulness. A generic chatbot is easy to demo but risky in the field. AgriMesh therefore separates model reasoning from factual tool data, uses retrieval before advising, stores outcomes as structured memory, and verifies every response before sending it. The technical choices are deliberately boring where reliability matters: FastAPI for service structure, SQLAlchemy for persistence, Ollama for local Gemma 4 inference, schema-constrained outputs for predictable model behavior, and explicit MCP-style services for tool boundaries.

AgriMesh is not claiming to replace certified agronomists, KVK officers, pesticide labels, or government systems. It is a first-line decision-support layer that can scale routine triage, preserve evidence trails, and escalate uncertainty. For judges, the proof is in the repository: the architecture is implemented, the Gemma 4 capabilities are exposed, and the agent is built to turn every farmer interaction into safer, more local, more useful agricultural intelligence.

## Attachments To Add In Kaggle

- Video: add the public YouTube demo link, 3 minutes or less.
- Public code repository: https://github.com/Utkarsh-Sinha0/AgentAgri
- Live demo: add the public demo URL or upload working demo files.
- Media Gallery: attach a cover image and relevant screenshots or video.
