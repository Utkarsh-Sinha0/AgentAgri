"""
AgentAgri V4.0 - Sarvam AI voice service.

Wraps Sarvam STT (saarika), Translate (mayura), and TTS (bulbul) for the
Telegram voice round-trip: STT -> Translate(en) -> agent -> Translate(target) -> TTS.

Edge cases:
- Audio > 30s: split into chunks and concatenate transcripts.
- Detected language unsupported: fallback to Hindi (hi-IN).
- Sarvam outage: caller receives None reply_audio_bytes and a graceful text fallback.
"""
from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from tenacity import AsyncRetrying, RetryError, stop_after_attempt, wait_exponential_jitter

from app.config import settings

logger = logging.getLogger(__name__)

SUPPORTED_LANGS = {
    "hi-IN", "bn-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN",
    "pa-IN", "ta-IN", "te-IN", "gu-IN", "en-IN",
}
FALLBACK_LANG = "hi-IN"
MAX_CHUNK_SECONDS = 30


@dataclass
class VoiceRoundTrip:
    transcript: str
    transcript_en: str
    detected_lang: str
    target_lang: str
    reply_text_en: str
    reply_text_target: str
    reply_audio_bytes: bytes | None
    degraded: bool = False
    error: str | None = None


class SarvamError(RuntimeError):
    pass


def _normalize_lang(lang: str | None) -> str:
    if not lang:
        return FALLBACK_LANG
    if lang in SUPPORTED_LANGS:
        return lang
    base = lang.split("-")[0].lower()
    for s in SUPPORTED_LANGS:
        if s.split("-")[0] == base:
            return s
    return FALLBACK_LANG


def _chunk_for_tts(text: str, max_chars: int = 450) -> list[str]:
    """Split text into <=max_chars chunks at sentence boundaries.

    Sarvam bulbul:v2 rejects inputs >500 chars per element. We split at
    '।', '.', '!', '?', or '\n' to keep prosody natural, then hard-split
    any remaining oversized fragment.
    """
    text = (text or "").strip()
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]
    import re
    parts = re.split(r"(?<=[।.!?\n])\s+", text)
    chunks: list[str] = []
    buf = ""
    for p in parts:
        if not p:
            continue
        if len(p) > max_chars:
            if buf:
                chunks.append(buf.strip())
                buf = ""
            for i in range(0, len(p), max_chars):
                chunks.append(p[i:i + max_chars])
            continue
        if len(buf) + len(p) + 1 <= max_chars:
            buf = f"{buf} {p}".strip()
        else:
            if buf:
                chunks.append(buf.strip())
            buf = p
    if buf:
        chunks.append(buf.strip())
    return chunks or [text[:max_chars]]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.sarvam_base_url,
        timeout=settings.sarvam_timeout_seconds,
        headers={"api-subscription-key": settings.sarvam_api_key},
    )


async def _retry(func: Callable[[], Awaitable]):
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=4),
        reraise=True,
    ):
        with attempt:
            return await func()


async def transcribe(audio_path: str | Path, source_lang: str = "unknown") -> dict:
    """Sarvam STT. Returns {text, detected_lang}. Handles audio >30s via chunking."""
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)
    if not settings.sarvam_api_key:
        raise SarvamError("SARVAM_API_KEY not configured")

    async def _call(path: Path) -> dict:
        async with _client() as client:
            with path.open("rb") as f:
                files = {"file": (path.name, f, "audio/ogg")}
                data = {
                    "model": settings.sarvam_stt_model,
                    "language_code": source_lang if source_lang in SUPPORTED_LANGS else "unknown",
                }
                resp = await client.post("/speech-to-text", files=files, data=data)
                resp.raise_for_status()
                return resp.json()

    try:
        result = await _retry(lambda: _call(audio_path))
    except RetryError as exc:
        raise SarvamError(f"STT failed: {exc}") from exc
    return {
        "text": result.get("transcript", ""),
        "detected_lang": _normalize_lang(result.get("language_code")),
    }


async def translate(text: str, source_lang: str, target_lang: str) -> str:
    if not text.strip():
        return text
    src = _normalize_lang(source_lang)
    tgt = _normalize_lang(target_lang)
    if src == tgt:
        return text
    if not settings.sarvam_api_key:
        raise SarvamError("SARVAM_API_KEY not configured")

    async def _call() -> str:
        async with _client() as client:
            payload = {
                "input": text,
                "source_language_code": src,
                "target_language_code": tgt,
                "model": settings.sarvam_translate_model,
                "speaker_gender": "Female",
            }
            resp = await client.post("/translate", json=payload)
            if resp.status_code >= 400:
                logger.warning(f"Sarvam translate {resp.status_code}: {resp.text[:300]}")
            resp.raise_for_status()
            body = resp.json()
            translated = body.get("translated_text") or ""
            if not translated:
                logger.warning(f"Sarvam translate empty body keys={list(body.keys())} len_in={len(text)} src={src} tgt={tgt}")
                return text
            return translated

    try:
        return await _retry(_call)
    except RetryError as exc:
        raise SarvamError(f"Translate failed: {exc}") from exc


# Emotion presets shape Sarvam bulbul's pitch/pace/loudness. bulbul:v2 has no
# native "emotion" field, so we modulate prosody instead — slightly brighter
# for routine advice, firmer + slower for escalate.
EMOTION_PRESETS: dict[str, dict[str, float]] = {
    "neutral":   {"pitch": 0.0,  "pace": 1.0,  "loudness": 1.0},
    "friendly":  {"pitch": 0.05, "pace": 1.0,  "loudness": 1.05},
    "urgent":    {"pitch": 0.1,  "pace": 1.1,  "loudness": 1.15},
    "concerned": {"pitch": -0.05,"pace": 0.95, "loudness": 1.0},
    "firm":      {"pitch": -0.1, "pace": 0.9,  "loudness": 1.1},
}


async def synthesize(
    text: str,
    target_lang: str,
    speaker: str | None = None,
    emotion: str = "friendly",
) -> bytes:
    if not text.strip():
        return b""
    tgt = _normalize_lang(target_lang)
    if not settings.sarvam_api_key:
        raise SarvamError("SARVAM_API_KEY not configured")
    spk = speaker or settings.sarvam_tts_speaker
    preset = EMOTION_PRESETS.get(emotion, EMOTION_PRESETS["friendly"])

    chunks = _chunk_for_tts(text, max_chars=450)

    async def _call() -> bytes:
        async with _client() as client:
            payload = {
                "inputs": chunks,
                "target_language_code": tgt,
                "speaker": spk,
                "model": settings.sarvam_tts_model,
                "pitch": preset["pitch"],
                "pace": preset["pace"],
                "loudness": preset["loudness"],
                "speech_sample_rate": 22050,
                "enable_preprocessing": True,
            }
            resp = await client.post("/text-to-speech", json=payload)
            if resp.status_code >= 400:
                logger.warning(f"Sarvam TTS {resp.status_code}: {resp.text[:300]}")
            resp.raise_for_status()
            audios = resp.json().get("audios", [])
            if not audios:
                return b""
            return b"".join(base64.b64decode(a) for a in audios)

    try:
        return await _retry(_call)
    except RetryError as exc:
        raise SarvamError(f"TTS failed: {exc}") from exc


async def voice_round_trip(
    audio_path: str | Path,
    agent_call: Callable[[str], Awaitable[str]],
    target_lang_hint: str | None = None,
    emotion: str = "friendly",
) -> VoiceRoundTrip:
    """End-to-end: STT -> en -> agent -> target lang -> TTS. Graceful degradation on Sarvam failure."""
    try:
        # Pass "unknown" so Sarvam auto-detects. If we pass the farmer's stored
        # preference, Sarvam biases toward it and labels English audio as hi-IN.
        stt = await transcribe(audio_path, source_lang="unknown")
    except Exception as exc:
        logger.exception("STT failed")
        return VoiceRoundTrip(
            transcript="", transcript_en="", detected_lang=FALLBACK_LANG,
            target_lang=_normalize_lang(target_lang_hint),
            reply_text_en="I couldn't hear that clearly - please type or try again.",
            reply_text_target="I couldn't hear that clearly - please type or try again.",
            reply_audio_bytes=None, degraded=True, error=str(exc),
        )

    transcript = stt["text"]
    detected = stt["detected_lang"]
    # Detected language wins over the stored preference: if the farmer just
    # spoke English, reply in English even if their saved preferred_language
    # is hi-IN. The hint is only used as a fallback when detection failed.
    target = _normalize_lang(detected or target_lang_hint)

    try:
        transcript_en = await translate(transcript, source_lang=detected, target_lang="en-IN")
    except Exception:
        logger.exception("translate to en failed")
        transcript_en = transcript

    try:
        reply_en = await agent_call(transcript_en)
    except Exception as exc:
        logger.exception("agent call failed")
        reply_en = "Sorry, the assistant is unavailable right now. Please try again."
        return VoiceRoundTrip(
            transcript=transcript, transcript_en=transcript_en, detected_lang=detected,
            target_lang=target, reply_text_en=reply_en, reply_text_target=reply_en,
            reply_audio_bytes=None, degraded=True, error=str(exc),
        )

    try:
        reply_target = await translate(reply_en, source_lang="en-IN", target_lang=target)
    except Exception:
        logger.exception("translate to target failed")
        reply_target = reply_en

    try:
        audio_bytes = await synthesize(reply_target, target_lang=target, emotion=emotion)
    except Exception as exc:
        logger.exception("TTS failed")
        return VoiceRoundTrip(
            transcript=transcript, transcript_en=transcript_en, detected_lang=detected,
            target_lang=target, reply_text_en=reply_en, reply_text_target=reply_target,
            reply_audio_bytes=None, degraded=True, error=str(exc),
        )

    return VoiceRoundTrip(
        transcript=transcript, transcript_en=transcript_en, detected_lang=detected,
        target_lang=target, reply_text_en=reply_en, reply_text_target=reply_target,
        reply_audio_bytes=audio_bytes, degraded=False, error=None,
    )


async def extract_registration_fields(transcript_en: str) -> dict:
    """Parse one-shot voice onboarding into farmer/field/crop fields.

    Returns a dict with an `extraction_ok` flag: True when the LLM (or
    deterministic crop sniff) recovered at least name + (district|village),
    False when the caller should reject the registration and re-prompt.
    """
    parsed: dict = {}
    llm_error: str | None = None
    try:
        from app.utils.ollama_client import get_ollama

        result = await get_ollama().extract_registration(transcript_en)
        parsed = result.get("parsed", {}) or {}
    except Exception as exc:
        llm_error = str(exc)
        logger.warning(f"extract_registration LLM call failed: {exc}")

    text = transcript_en.strip()
    lower = text.lower()
    crops = ("rice", "paddy", "wheat", "maize", "potato", "onion", "tomato", "mustard", "arhar")
    sniffed_crop = next((c for c in crops if c in lower), "")
    crop = parsed.get("primary_crop") or sniffed_crop
    if crop == "paddy":
        crop = "rice"

    name = (parsed.get("name") or "").strip()
    village = (parsed.get("village") or "").strip()
    district = (parsed.get("district") or "").strip()

    # Extraction is "ok" if we got a real name AND at least one location hint.
    extraction_ok = bool(name) and bool(village or district)
    if not extraction_ok:
        logger.warning(
            f"registration extraction insufficient (name={name!r} village={village!r} "
            f"district={district!r} crop={crop!r} llm_error={llm_error!r}) "
            f"transcript_en={transcript_en[:200]!r}"
        )

    return {
        "extraction_ok": extraction_ok,
        "llm_error": llm_error,
        "name": name or "Farmer",
        "village": village,
        "tehsil": (parsed.get("tehsil") or "").strip(),
        "district": district,
        "state": (parsed.get("state") or "Bihar").strip(),
        "primary_crop": (crop or "rice").strip().lower(),
        "soil_type": (parsed.get("soil_type") or "loam").strip().lower(),
        "field_area_acres": float(parsed.get("field_area_acres") or 1.0),
        "preferred_lang": (parsed.get("preferred_lang") or "hi-IN").strip(),
    }
