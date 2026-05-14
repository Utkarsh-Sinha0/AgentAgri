"""
AgriMesh V4.0 — Degradation Ladder & Circuit Breaker (§7)
Six levels, auto-switching based on health checks every 60 seconds.
Level 5: Full Gemma (Vision + Reasoning + Tools + Wiki)
Level 4: Gemma text-only (no vision)
Level 3: Gemma without Wiki
Level 2: E2B model + Wiki
Level 1: Deterministic engine + Wiki HTML (template-based)
Level 0: Pre-canned symptom lookup
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

from loguru import logger

from app.config import settings


class DegradationLevel(IntEnum):
    """Higher = better quality. System auto-downgrades on failures."""
    PRECANNED = 0       # Static symptom lookup — always works
    DETERMINISTIC = 1   # Template engine + Wiki HTML
    E2B_WIKI = 2        # E2B model + Wiki retrieval
    E4B_NO_WIKI = 3     # E4B text-only, no wiki
    E4B_TEXT = 4        # E4B text + wiki, no vision
    FULL = 5            # Full E4B: Vision + Reasoning + Tools + Wiki


@dataclass
class HealthStatus:
    level: DegradationLevel = DegradationLevel.FULL
    ollama_healthy: bool = False
    ollama_model_available: str = ""
    vision_healthy: bool = False
    wiki_available: bool = False
    tools_healthy: bool = False
    db_healthy: bool = False
    latency_ms: int = 0
    last_checked: float = 0.0
    consecutive_failures: int = 0
    degraded_at: float | None = None


class CircuitBreaker:
    """
    Monitors system health and auto-degrades/upgrades based on failures.
    Checks every 60 seconds. Three consecutive failures → degrade one level.
    Three consecutive successes → attempt upgrade one level.
    """

    def __init__(self):
        self.status = HealthStatus()
        self._lock = asyncio.Lock()
        self._upgrade_handlers: dict[DegradationLevel, Callable | None] = {
            DegradationLevel.PRECANNED: None,
            DegradationLevel.DETERMINISTIC: None,
            DegradationLevel.E2B_WIKI: None,
            DegradationLevel.E4B_NO_WIKI: None,
            DegradationLevel.E4B_TEXT: None,
            DegradationLevel.FULL: None,
        }

    @property
    def current_level(self) -> DegradationLevel:
        return self.status.level

    async def check_health(self) -> HealthStatus:
        """Run all health checks concurrently."""
        async with self._lock:
            t0 = time.perf_counter()
            checks = await asyncio.gather(
                self._check_ollama(),
                self._check_db(),
                self._check_wiki(),
                self._check_tools(),
                return_exceptions=True,
            )

            ollama_result, db_result, wiki_result, tools_result = checks

            self.status.ollama_healthy = not isinstance(ollama_result, Exception) and ollama_result
            self.status.db_healthy = not isinstance(db_result, Exception) and db_result
            self.status.wiki_available = not isinstance(wiki_result, Exception) and wiki_result
            self.status.vision_healthy = self.status.ollama_healthy  # Vision needs Ollama
            self.status.tools_healthy = not isinstance(tools_result, Exception) and tools_result
            self.status.latency_ms = int((time.perf_counter() - t0) * 1000)
            self.status.last_checked = time.time()

            # Determine level
            self._recalculate_level()

            return self.status

    async def _check_ollama(self) -> bool:
        """Ping Ollama to check connectivity."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{settings.ollama_host}/api/tags")
                if resp.status_code == 200:
                    data = resp.json()
                    models = [m["name"] for m in data.get("models", [])]
                    self.status.ollama_model_available = (
                        settings.ollama_model if settings.ollama_model in models
                        else settings.ollama_fallback_model if settings.ollama_fallback_model in models
                        else ""
                    )
                    return bool(self.status.ollama_model_available)
                return False
        except Exception:
            return False

    async def _check_db(self) -> bool:
        """Check database connectivity."""
        try:
            from sqlalchemy import text

            from app.database import async_session_factory
            async with async_session_factory() as db:
                await db.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def _check_wiki(self) -> bool:
        """Check if wiki articles exist."""
        try:
            from sqlalchemy import func, select

            from app.database import async_session_factory
            from app.models import WikiArticle
            async with async_session_factory() as db:
                count = (await db.execute(select(func.count(WikiArticle.id)))).scalar()
            return count > 0
        except Exception:
            return False

    async def _check_tools(self) -> bool:
        """Run one lightweight MCP-backed tool path instead of assuming tools are healthy."""
        try:
            from app.services.weather import get_forecast

            result = await asyncio.wait_for(get_forecast(field_id="default", days=1), timeout=2.0)
            return bool(result.get("forecast"))
        except Exception:
            return False

    def _recalculate_level(self):
        """Determine current degradation level based on health checks."""
        if not self.status.db_healthy:
            self.status.level = DegradationLevel.PRECANNED
        elif not self.status.ollama_healthy:
            self.status.level = DegradationLevel.DETERMINISTIC
        elif not self.status.vision_healthy:
            self.status.level = DegradationLevel.E4B_TEXT
        elif not self.status.wiki_available:
            self.status.level = DegradationLevel.E4B_NO_WIKI
        else:
            # Try to use the best available model
            if settings.ollama_model in (self.status.ollama_model_available or ""):
                self.status.level = DegradationLevel.FULL
            elif settings.ollama_fallback_model in (self.status.ollama_model_available or ""):
                self.status.level = DegradationLevel.E2B_WIKI
            else:
                self.status.level = DegradationLevel.E4B_TEXT

    async def record_success(self):
        """Record a successful operation. May trigger upgrade."""
        self.status.consecutive_failures = 0
        if self.status.level < DegradationLevel.FULL and self.status.degraded_at:
            # Check if we should upgrade
            await self.check_health()

    async def record_failure(self):
        """Record a failure. May trigger degradation."""
        self.status.consecutive_failures += 1
        if self.status.consecutive_failures >= 3 and self.status.level > DegradationLevel.PRECANNED:
            old_level = self.status.level
            self.status.level = DegradationLevel(max(0, int(self.status.level) - 1))
            self.status.degraded_at = time.time()
            logger.warning(
                f"Circuit breaker: degraded {old_level.name} → {self.status.level.name} "
                f"({self.status.consecutive_failures} consecutive failures)"
            )

    def can_handle_vision(self) -> bool:
        return self.status.level >= DegradationLevel.FULL and self.status.vision_healthy

    def can_use_tools(self) -> bool:
        return self.status.level >= DegradationLevel.E4B_NO_WIKI and self.status.tools_healthy

    def can_retrieve_wiki(self) -> bool:
        return self.status.wiki_available

    def get_fallback_response(self, query: str) -> dict:
        """Level 0/1 fallback: pre-canned symptom lookup."""
        symptoms = {
            "brown_spots": {
                "risk": "PREVENTIVE_ACTION",
                "actions": [
                    "Check if spots are diamond-shaped (blast) or oval with yellow halo (brown spot).",
                    "Reduce nitrogen fertilizer if leaves are dark green.",
                    "Improve field drainage — standing water increases disease risk.",
                    "Contact your local KVK for in-person diagnosis.",
                ],
                "context": "भूरे धब्बे कई बीमारियों के लक्षण हो सकते हैं। कृपया अपने नजदीकी KVK से संपर्क करें।",
            },
            "yellow_leaves": {
                "risk": "WATCH",
                "actions": [
                    "Yellowing may indicate nitrogen deficiency (uniform) or potassium deficiency (leaf tips/margins).",
                    "Get your soil tested at the nearest KVK.",
                    "Apply balanced NPK fertilizer based on soil test results.",
                ],
                "context": "पत्तियों का पीला होना पोषक तत्वों की कमी का संकेत हो सकता है। मिट्टी परीक्षण करवाएं।",
            },
            "insects": {
                "risk": "PREVENTIVE_ACTION",
                "actions": [
                    "Identify the insect — look for stem borer holes, leaf folder damage, or aphids under leaves.",
                    "Install pheromone traps for monitoring.",
                    "Consider neem oil spray (5ml/L) as first-line treatment.",
                ],
                "context": "कीटों की पहचान करें। नीम तेल का छिड़काव पहला उपाय हो सकता है।",
            },
            "water_damage": {
                "risk": "ESCALATE",
                "actions": [
                    "Dig emergency drainage channels immediately.",
                    "Do NOT apply any fertilizer until water recedes.",
                    "Contact your extension worker for damage assessment.",
                ],
                "context": "जलभराव से फसल को गंभीर नुकसान हो सकता है। तुरंत जल निकासी की व्यवस्था करें।",
            },
            "default": {
                "risk": "WATCH",
                "actions": [
                    "Monitor your crop daily for changes in symptoms.",
                    "Take clear photos and share with your extension worker.",
                    "Contact Kisan Call Center: 1800-180-1551",
                ],
                "context": "कृपया अपनी फसल की निगरानी करें और लक्षणों में बदलाव पर ध्यान दें। किसान कॉल सेंटर: 1800-180-1551",
            },
        }

        ql = query.lower()
        for key, response in symptoms.items():
            if key.replace("_", " ") in ql or key in ql:
                return response

        return symptoms["default"]


# ─── Singleton ────────────────────────────────────────────────────────

_circuit_breaker: CircuitBreaker | None = None

def get_circuit_breaker() -> CircuitBreaker:
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = CircuitBreaker()
    return _circuit_breaker


async def health_monitor_loop(interval: int = 60):
    """Background loop: check health every N seconds."""
    cb = get_circuit_breaker()
    logger.info(f"Health monitor started (interval={interval}s)")
    while True:
        try:
            status = await cb.check_health()
            logger.debug(
                f"Health: level={status.level.name} ollama={status.ollama_healthy} "
                f"db={status.db_healthy} wiki={status.wiki_available} "
                f"latency={status.latency_ms}ms"
            )
        except Exception as exc:
            logger.error(f"Health check failed: {exc}")
        await asyncio.sleep(interval)
