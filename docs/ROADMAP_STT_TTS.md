# Roadmap — Speech-to-Text (STT) & Text-to-Speech (TTS)

> **Status:** Phase 2 / future. Not blocking the hackathon demo. Specs of the existing agent are locked — voice is an *additional channel*, the agent itself does not change.

The Munger pilot farmers have low text-literacy. A reliable Hindi (and ideally Bhojpuri) voice loop is the single highest-leverage UX improvement for the next phase. This doc lays out the integration plan, candidates to evaluate, and acceptance bars.

---

## Phasing

### Phase A — Inbound voice (STT only)
Farmer sends a voice note. We transcribe it. The rest of the pipeline (intent → retrieval → … → reply) is unchanged. Reply stays as Hindi text.

**Smallest shippable unit.** Lets us collect real Hindi voice data while keeping the response path simple.

### Phase B — Outbound voice (TTS)
Hindi text reply → synthesised Hindi voice note delivered alongside the text. Farmer can listen or read.

### Phase C — End-to-end voice loop
Voice in, voice out. Text becomes a fallback / debug channel. This is the product North Star.

### Phase D — Conversational duplex (later)
Streaming STT + barge-in + interruption handling. Out of scope for the next 6 months — listed only so we don't accidentally architect ourselves out of it.

---

## Candidates to evaluate

Pick at least three from this list, benchmark on real Munger Hindi data, then choose. **Don't pick on hype** — pick on word-error-rate (WER), latency, cost per minute, and on-device feasibility.

### Hosted APIs

| Service | Languages | Strengths | Notes |
|---|---|---|---|
| **Sarvam AI** (`sarvam.ai`) | Hindi, Bhojpuri, Tamil, Telugu, Kannada, Marathi, Punjabi, Bengali, Gujarati, Malayalam, Odia | India-focused, Bhojpuri support is rare, low latency from India | First-choice candidate. Verify pricing as of integration date. |
| **Bhashini** (govt-of-India ULCA) | All 22 scheduled languages | Free / subsidised for public-good use, government-aligned | Quality varies by language pair; verify Hindi-Bhojpuri before committing |
| **Google Cloud Speech-to-Text** | Hindi (very good), Bhojpuri (no) | Mature, predictable latency | Cost climbs at scale |
| **OpenAI Whisper API** | Hindi (excellent multilingual) | Single endpoint, well-known | Higher latency, USD pricing |

### Open-source / self-host

| Model | Type | Notes |
|---|---|---|
| **faster-whisper** (CTranslate2 build of Whisper large-v3) | STT | Runs on CPU at ~3× realtime, GPU much faster. WER on Hindi ~10–13%. |
| **AI4Bharat IndicConformer** | STT | Trained on Indian languages, free, hosted on HF. Worth comparing. |
| **AI4Bharat Indic-Parler-TTS** | TTS | High-quality Hindi TTS, MIT-licensed. |
| **Coqui XTTS-v2** | TTS | Voice-cloning capable. Licence is non-commercial — avoid for paid usage. |
| **Piper** (rhasspy/piper) | TTS | Tiny CPU footprint. Hindi voice models exist but quality is modest. |
| **Gemma 4 native audio** | STT (likely) | `USE_GEMMA_AUDIO` flag already exists in `.env.example`. Once Ollama exposes Gemma 4's audio capability stably, this becomes the cheapest option (no extra model to ship). Defer until upstream support is solid. |

---

## Architectural fit

The agent currently expects `AgentContext.message: str` and replies with `AgentResponse.display_text: str`. Voice integrates as a thin shell around this — the agent itself does not change.

```
                    ┌──────────────────────────────┐
                    │   Telegram voice message     │
                    └──────────────┬───────────────┘
                                   │ (Phase A starts here)
                       ┌───────────▼────────────┐
                       │ services/stt.py        │
                       │ transcribe(audio_path) │
                       └───────────┬────────────┘
                                   │ Hindi text
                       ┌───────────▼────────────┐
                       │ existing agent.process │   ← unchanged
                       └───────────┬────────────┘
                                   │ Hindi text reply
                       ┌───────────▼────────────┐
                       │ services/tts.py        │   ← Phase B
                       │ synthesise(text)       │
                       └───────────┬────────────┘
                                   │ audio file
                       ┌───────────▼────────────┐
                       │   Telegram voice send  │
                       └────────────────────────┘
```

### Files to add

| New file | Purpose |
|---|---|
| `app/services/stt.py` | One async function `transcribe(audio_path: Path, language_hint: str) -> str`. Provider-agnostic interface; provider selected by env var. |
| `app/services/tts.py` | `synthesise(text: str, voice: str = "hi-female-1") -> Path` returns a path to an `.ogg` ready for Telegram. |
| `app/services/voice_router.py` | Glue: decides text vs voice reply based on user preference + last input modality. |
| `tests/test_voice_flows.py` | Fixture audio in `tests/fixtures/audio/`, mock STT/TTS providers, end-to-end voice-in-text-out and voice-in-voice-out flows. |
| `docs/voice_provider_evaluation.md` | (Owned by the integrator) WER + latency + cost table for each candidate on the Munger test set. |

### Files to extend

| File | What changes |
|---|---|
| `app/bot/telegram_bot.py` | Add `MessageHandler(filters.VOICE, handle_voice)`. The handler downloads the audio, calls `stt.transcribe`, then funnels into the existing text path. |
| `app/services/degradation.py` | If STT provider fails, send a Hindi message asking the farmer to type instead. If TTS fails, send text-only. Never block the advisory on voice success. |
| `app/config.py` | New settings: `STT_PROVIDER`, `STT_API_KEY`, `TTS_PROVIDER`, `TTS_API_KEY`, `VOICE_REPLY_DEFAULT`, plus the existing `ENABLE_VOICE_STT` and `USE_GEMMA_AUDIO` flags. |
| `.env.example` | Document the new keys. |

---

## Evaluation protocol

Before committing to any provider, the integrator must produce `docs/voice_provider_evaluation.md` with:

1. **A test set** of at least 30 real (anonymised) Hindi voice notes from rural Bihar speakers. Mix of clean, noisy (background tractor / wind), short (5 s), long (45 s), Bhojpuri-tinted.
2. **Per-provider metrics:**
   - WER against gold transcripts (compute with `jiwer`)
   - Median latency, p95 latency (from request to final transcript)
   - Cost per minute of audio
   - Bhojpuri handling (qualitative — does it transcribe at all? does it return Hindi best-effort?)
3. **A recommendation paragraph** with a clear primary pick + a documented fallback for when the primary is down.
4. **A reproducibility script** in `app/scripts/eval_stt.py` so we can re-run on every new provider release.

---

## Acceptance bar (per phase)

### Phase A acceptance
- A farmer sends a 30 s Hindi voice note. The bot replies with a correct Hindi text advisory within p95 ≤ 90 s end-to-end.
- WER ≤ 20% on the Munger test set.
- If STT fails, the farmer gets a polite Hindi text asking them to type — never a stack trace, never silence.
- 5+ new tests in `tests/test_voice_flows.py` covering: happy path, garbled audio, STT-down fallback, Bhojpuri input, very long audio.

### Phase B acceptance
- Bot reply is delivered as a voice note within p95 ≤ 12 s after the text reply is generated.
- Voice is intelligible at 8 kHz playback (the realistic phone speaker).
- If TTS fails, text-only delivery proceeds with no user-visible error.
- Synthesised audio is cached by reply hash (no synthesising the same string twice).

### Phase C acceptance
- Round-trip voice-in / voice-out p95 ≤ 100 s end-to-end on the Munger test set.
- The Telegram-bot operator can toggle a user between voice-default and text-default via `/voice on|off`.
- Real-pilot logs show ≥70% of voice-mode farmers successfully completing a turn without falling back to text.

---

## Non-goals (for now)

- Real-time streaming STT (Phase D)
- Speaker diarisation (one-farmer-per-chat assumption is fine)
- Voice cloning of specific extension workers
- TTS in Bhojpuri (Hindi-only is acceptable for Phase B; Bhojpuri TTS is a stretch goal)
- Wake-word / always-on listening

---

## Risks and unknowns

| Risk | Mitigation |
|---|---|
| Bhojpuri WER is too high on every provider | Fall back to Hindi-pronounced reply with Bhojpuri vocabulary substitution at the LLM layer (prompt engineering, not STT). |
| Voice notes are too long (>2 min) — cost explodes | Cap input to 60 s; politely ask the farmer to split. |
| TTS reply sounds robotic and farmers ignore it | Compare Sarvam vs Indic-Parler-TTS on naturalness blind test with 5 native speakers. |
| Telegram audio format conversion (`.ogg` ↔ `.wav`) adds latency | Pre-warm `pydub` / `ffmpeg`; cache converted outputs. |
| Provider API changes without notice | Pin SDK versions in `requirements.txt`; integration tests against recorded fixtures, not live API, in CI. |

---

## When to start

This work is **blocked on**:
1. Hackathon demo shipping (text-only is sufficient for demo day)
2. At least one pilot farmer testing the text bot end-to-end and reporting voice would be more usable

Once both are true, start with Phase A and the provider evaluation. Don't skip the evaluation step — picking a voice provider on vibes will cost weeks later.
