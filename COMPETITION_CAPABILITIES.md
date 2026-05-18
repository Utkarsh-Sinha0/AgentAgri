# Gemma 4 — Five Capabilities Proven in AgriMesh V4.0

AgriMesh exercises **all five** Gemma 4 differentiator capabilities in production paths.
Each capability fires through `app/utils/ollama_client.py` and is logged to an in-process
ring buffer (`app/services/capability_log.py`) so judges can verify them live.

## Where to see proof, end-to-end

- **Live endpoint**: `GET /api/v1/capabilities` — JSON snapshot of counts + most-recent event per capability.
- **PWA tab**: `/gemma4` route — 5-card matrix lights up green as each capability fires.
- **Telegram bot**: every advisory ends with a footer like `Gemma 4: 🧠 🛠 📷 📐 🌐 5/5`.

## Capability matrix

| # | Capability | Where it fires | Proof artefact | Code |
|---|---|---|---|---|
| 1 | **Thinking mode** (native `<\|think\|>` token) | ReAct tool planning (`plan_tools`), cluster summary | Event `thinking` recorded with model + schema | `ollama_client.py: chat()` `thinking=True` branch; `<\|think\|>` prepended to last user msg (preserves KV cache vs. system-prompt injection) |
| 2 | **Function calling** | Agent decides which MCP tools to run (`get_forecast`, `get_mandi_prices`, `match_schemes`, `get_msp`, `get_historical_weather`) | Event `function_call` with comma-separated tool names | `ollama_client.py: plan_tools()` parses grammar-constrained `tool_call` schema |
| 3 | **Multimodal vision** | Crop photo analysis (image **before** text — Gemma 4 best practice for 5–10% accuracy boost) | Event `multimodal` with image filename | `ollama_client.py: analyze_crop_photo()` |
| 4 | **Grammar-constrained decoding** | Every structured call: intent classification, tool planning, template selection, registration extraction, safety check, cluster summary | Event `grammar` with schema name | `ollama_client.py: chat()` passes JSON schema as Ollama `format=` → llama.cpp GBNF |
| 5 | **Multilingual (Hindi+English)** | Farmer messages in Devanagari or Latin script handled natively; bilingual reply rendering | Event `multilingual` when Devanagari detected in user content | `ollama_client.py: chat()` post-call Unicode-range check (`ऀ`–`ॿ`) |

## Why these matter for smallholder agri advisory

- **Thinking mode** lets the agent deliberate about *which tool* to call before answering — fewer wasted MCP roundtrips for finance / weather / scheme queries.
- **Function calling** keeps advisories grounded in live data (mandi prices, MSP, forecasts) instead of hallucinated numbers.
- **Multimodal** is the difference between "send a photo, I'll look" and "describe your problem in words" — critical when the farmer's literacy or typing speed is the bottleneck.
- **Grammar-constrained decoding** removes the need for fragile regex-on-LLM-output. Schemas live in `app/schemas/*.schema.json`; broken JSON → automatic retry → empty dict (never crashes the advisory pipeline).
- **Multilingual** ships *natively* — no translation hop required for input understanding. (Sarvam handles output TTS for emotion + dialect.)

## How to reproduce the proof on judge laptop

1. `./venv/Scripts/uvicorn.exe app.main:app --reload`
2. Open Telegram bot, `/start` → `/register` (fill name, district, pincode, tehsil, village).
3. Send a Hindi message with a crop photo, e.g. `मेरी धान में पीले धब्बे हो रहे हैं` + photo.
4. Open `/dashboard` link from bot → PWA opens authenticated.
5. Click **Gemma 4** tab → all 5 cards show `proven` with non-zero counts within ~3s.
6. Optional: `curl http://localhost:8000/api/v1/capabilities | jq` for raw JSON.

## Implementation notes

- Ring buffer holds the last 200 events; safe under threading lock; zero external deps.
- Detection is conservative — `multilingual` only fires on Devanagari script, not "Hinglish" Latin transliteration, so the count is a lower bound on real multilingual usage.
- The footer in bot replies (`Gemma 4: 🧠 🛠 📷 📐 🌐 N/5`) updates *per-reply*, so judges can watch it grow from `1/5` (intent classification only) to `5/5` after sending a Hindi + photo + scheme query.

## File pointers

- `app/services/capability_log.py` — ring buffer + snapshot API.
- `app/utils/ollama_client.py` — `chat()`, `plan_tools()`, `analyze_crop_photo()` instrumentation.
- `app/main.py` — `GET /api/v1/capabilities`.
- `pwa/src/main.jsx` — `Gemma4Page` component.
- `app/bot/telegram_bot.py` — footer assembly in the advisory reply path.
