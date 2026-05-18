# Architecture: How We Use Gemma 4 in AgriMesh

> **For judges — read this first.** Every Gemma 4 capability the competition highlights is wired into a production AgriMesh feature with farmer-visible behavior. This page is the proof, line by line.

---

## Table 1 — Gemma 4 capability → AgriMesh code → live demo step

| Gemma 4 capability | Where in the code | Demo command that proves it | What you will see |
|---|---|---|---|
| **Configurable Thinking Mode** | `app/services/agent.py` (`thinking_enabled` toggle, ReAct planner that returns empty on trivial intents) | `/demo 6` "Are other farmers in my district seeing brown spot?" | Bot enters thinking mode, reasons over multi-village cluster aggregates with k-anonymity ≥3, returns a structured aggregated answer with reasoning steps in the trace footer. |
| **128K Context Window** | `app/services/memory.py` (`_compose_full_field_context`), `app/services/agent.py` (prompt assembly) | `/demo 3` "What was my last NPK dose and did it help?" | Bot retrieves 180 days of observations + 16 weeks of NDVI + finance ledger + follow-up loops, formats them into one prompt, recalls the urea split timing and links the day-30 advisory back to the day-78 outcome. |
| **MoE & PLE architecture** | Gemma 4 model itself (we run `gemma4:e4b` primary, `gemma4:e2b` fallback via Ollama at `app/utils/ollama_client.py`) | Footer of every reply shows `model · confidence` | Watch the model name flip between e4b (heavy queries) and e2b (degraded mode). Per-Layer Embeddings keep VRAM under 9 GB so the bot runs on the same GPU that powers a local PWA. |
| **Variable-Resolution Image Budget** | `app/services/agent.py` (photo path), `app/services/voice.py` (multimodal envelope), Telegram `handle_photo` in `app/bot/telegram_bot.py` | `/demo 1` send a leaf photo with caption "what's wrong?" | High-res for disease photos (256x256 patches), low-res for crowd shots; lesion ID uses the high-budget path, scenery uses low. Disease cite goes to NIPHM rice IPM PDF. |
| **Native Audio Processing** | `app/services/voice.py` (Sarvam STT/TTS bridge, language auto-detect, 450-char TTS chunking, dual-script reply) | `/demo 2` send a Hindi or Bhojpuri voice note | Bot transcribes without language hint, detected language wins over stored preference, Gemma answers in same language, TTS sends back audio + text. |
| **Object Detection & Pointing** | `app/services/agent.py` (photo + pointing query path), `app/services/verifier.py` (location citation requirement) | `/demo 7` photo of pest with caption "what's on the underside of this leaf?" | Bot points at the lower stem region, identifies brown planthopper, returns IPM card with non-pyrethroid warning. |
| **Function Calling** | Five MCP servers (`app/mcp_servers/weather_server.py`, `mandi_server.py`, `scheme_server.py`, `finance_server.py`, `crop_kb_server.py`), tool-call schema in `app/services/agent.py` | `/demo 5` "Should I sell now or store?" | Bot chains mandi → storage → weather function calls, returns sell/store/wait decision with ROI math. Each tool call logged in `app/services/capability_log.py`. |
| **Grammar-Constrained Decoding** | `app/utils/ollama_client.py` (`USE_GRAMMAR_DECODING=1`), `app/services/agent.py` (intent classification grammar, tool-call grammar) | `/demo 4` "MSP of rice in Patna" | Intent classifier emits one of 8 fixed labels — never hallucinated. MSP figure comes verbatim from `data/seed/msp_by_state_crop.json`, verifier rejects any answer without a source citation. |
| **Multilingual (11 Indic languages)** | `app/bot/telegram_bot.py` `/start` 11-lang picker, `app/services/voice.py` auto-detect, `app/services/agent.py` bilingual contextualization with `*— LANG —*` dividers | `/demo 2` (voice), `/demo 8` (text) in any of 11 languages | Gemma handles Hindi, Hinglish, Bhojpuri, Marathi, Bengali, Tamil, Telugu, Kannada, Punjabi, Gujarati, Odia, Malayalam directly — Sarvam is only the voice frontend, never the text translator. |

---

## Table 2 — Hackathon track → AgriMesh evidence → why we win this track

| Track | AgriMesh evidence | Why this is the winning answer |
|---|---|---|
| **Agriculture / Food Security** | 180-day field memory per farmer, sustainable-first IPM cited to NIPHM rice + PPQS wheat manuals, MSP/scheme/insurance cited from official seed files, k-anonymity cluster intel for early outbreak warnings | We don't just answer questions — we keep a farmer's *history*, link advice to outcomes, and warn neighbours of pest pressure before it spreads. Real production codepath, not a demo skin. |
| **Voice-First / Accessibility** | 11-language `/start` picker, Sarvam STT auto-detect, dual-script reply (text + audio) for Indic prefs, voice notes accepted in every FSM state including registration | A smallholder farmer in Munger never has to type. Hindi voice in → Hindi voice + text out, with the script also rendered so they can show their cooperative. |
| **Multimodal AI** | Photo + voice + text + NDVI + finance — all fused in one Gemma context. Disease photo → cited IPM PDF excerpt + IPM action card + follow-up loop opened automatically. | Vision and audio aren't bolt-ons; they share the same memory palace and the same verifier. A photo logged today is recalled by name 6 months from now. |
| **Responsible AI / Privacy** | Device-vs-server data partition (`docs/data-partition.md`), `/mydata` transparency dump, `/forgetme` soft-delete, k-anonymity ≥3 on every cluster query, no chat text ever routed through external translate | A farmer's session sees only their data + their memory atoms; aggregate intel is anonymized and gated. Hard floor, no override switch. |
| **Edge / Offline-Capable** | Gemma 4 e2b fallback when e4b OOM, graceful degradation (`app/services/degradation.py`) when Sarvam or Ollama down — never blank error, falls back to cached MSP + last-known text | Bot stays useful on a 4 GB Jetson. Voice fails → text fallback. Mandi API fails → seeded MSP. |
| **Indic Language AI** | Gemma 4 handles Hindi/Hinglish/Bhojpuri/9 more Indic scripts directly. Bilingual contextualization with `*— LANG —*` dividers. Never route text through Sarvam Translate. | The model itself is the translator. No double-hop latency, no translation drift. |

---

## Demo flow — recommended judge sequence

| Step | Command | Capability shown | Time |
|---|---|---|---|
| 0 | `/start`, pick language, register as yourself | Personalisation graft — your name binds to seeded farmer Ram Kumar's 180-day history | 90 s |
| 1 | `/demo 1` + leaf photo | Vision + variable-resolution + cited IPM | 30 s |
| 2 | `/demo 2` Hindi voice note | Native audio + auto-detect + dual-script reply | 45 s |
| 3 | `/demo 3` "what was my last urea dose?" | 128K context + long memory recall | 30 s |
| 4 | `/demo 4` "MSP of rice in Patna" | Grammar-constrained intent + verifier citation | 20 s |
| 5 | `/demo 5` "should I sell now?" | Function calling chain (mandi + storage + weather) | 60 s |
| 6 | `/demo 6` "are other farmers seeing brown spot?" | Thinking mode + k-anonymity cluster intel | 45 s |
| 7 | `/demo 7` pest photo + pointing query | Object detection + pointing | 45 s |
| 8 | `/demo 8` "plan my next 30 days" | Long context + thinking + tool-chain | 60 s |
| 9 | Open PWA `/` for the same farmer | Dashboard rendering NDVI dip, finance ROI, outbreak banner | 60 s |

Total: **~7 minutes** for the full Gemma 4 + AgriMesh story.

---

## What is *not* a Gemma demo trick — what is real production code

- The reversibility tag `baseline-2026-05-18` (commit `2f0a852`) anchors every change since.
- 211 tests in `tests/`, 75 %+ pass rate on golden KB evals, 62.5 %+ on synthetic evals.
- 5 MCP servers run as independent processes — function calling is real RPC, not in-process function dispatch.
- Verifier rejects MSP / insurance / scheme answers without a source citation. Hard gate, not advisory.
- K-anonymity gate ≥ 3 on every cluster query. Hard floor, not a config knob.

If a judge wants to break it: turn off Ollama mid-conversation. Bot keeps replying with cached MSP and seed weather. Turn off Sarvam. Voice fails to text. Turn off both. Bot returns "I cannot reach the model right now, here is what I last knew about your field." Graceful, not blank.

---

*See also: `docs/HANDOVER.md` §12 (feature freeze), `docs/ARCHITECTURE.md`, `docs/FEATURES.md`.*
