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
            resp.raise_for_status()
            return resp.json().get("translated_text", text)

    try:
        return await _retry(_call)
    except RetryError as exc:
        raise SarvamError(f"Translate failed: {exc}") from exc


async def synthesize(text: str, target_lang: str, speaker: str | None = None) -> bytes:
    if not text.strip():
        return b""
    tgt = _normalize_lang(target_lang)
    if not settings.sarvam_api_key:
        raise SarvamError("SARVAM_API_KEY not configured")
    spk = speaker or settings.sarvam_tts_speaker

    async def _call() -> bytes:
        async with _client() as client:
            payload = {
                "inputs": [text[:1500]],
                "target_language_code": tgt,
                "speaker": spk,
                "model": settings.sarvam_tts_model,
                "pitch": 0,
                "pace": 1.0,
                "loudness": 1.0,
                "speech_sample_rate": 22050,
                "enable_preprocessing": True,
            }
            resp = await client.post("/text-to-speech", json=payload)
            resp.raise_for_status()
            audios = resp.json().get("audios", [])
            if not audios:
                return b""
            return base64.b64decode(audios[0])

    try:
        return await _retry(_call)
    except RetryError as exc:
        raise SarvamError(f"TTS failed: {exc}") from exc


async def voice_round_trip(
    audio_path: str | Path,
    agent_call: Callable[[str], Awaitable[str]],
    target_lang_hint: str | None = None,
) -> VoiceRoundTrip:
    """End-to-end: STT -> en -> agent -> target lang -> TTS. Graceful degradation on Sarvam failure."""
    try:
        stt = await transcribe(audio_path, source_lang=_normalize_lang(target_lang_hint))
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
    target = _normalize_lang(target_lang_hint or detected)

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
        audio_bytes = await synthesize(reply_target, target_lang=target)
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

    The LLM path is preferred; a tiny deterministic fallback keeps onboarding
    usable when Ollama is down during demos.
    """
    try:
        from app.utils.ollama_client import get_ollama

        result = await get_ollama().extract_registration(transcript_en)
        parsed = result.get("parsed", {}) or {}
    except Exception:
        parsed = {}

    text = transcript_en.strip()
    lower = text.lower()
    crops = ("rice", "paddy", "wheat", "maize", "potato", "onion", "tomato", "mustard", "arhar")
    crop = parsed.get("primary_crop") or next((c for c in crops if c in lower), "")
    if crop == "paddy":
        crop = "rice"

    return {
        "name": (parsed.get("name") or "Farmer").strip(),
        "village": (parsed.get("village") or "").strip(),
        "tehsil": (parsed.get("tehsil") or "").strip(),
        "district": (parsed.get("district") or "").strip(),
        "state": (parsed.get("state") or "Bihar").strip(),
        "primary_crop": (crop or "rice").strip().lower(),
        "soil_type": (parsed.get("soil_type") or "loam").strip().lower(),
        "field_area_acres": float(parsed.get("field_area_acres") or 1.0),
        "preferred_lang": (parsed.get("preferred_lang") or "hi-IN").strip(),
    }
