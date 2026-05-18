"""
AgriMesh V4.0 — Gemma 4 capability event log.

In-process ring buffer of capability events emitted by ollama_client and other
callers. Used by /api/v1/capabilities to prove the 5 Gemma 4 capabilities to
judges: thinking, function-calling, multimodal, grammar-constrained, multilingual.
"""
from __future__ import annotations

import threading
import time
from collections import Counter, deque
from typing import Any

_CAPABILITIES = (
    "thinking",
    "function_call",
    "multimodal",
    "grammar",
    "multilingual",
)

_MAX_EVENTS = 200
_events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
_lock = threading.Lock()


def record(capability: str, *, model: str = "", detail: str = "", latency_ms: int | None = None) -> None:
    if capability not in _CAPABILITIES:
        return
    evt = {
        "capability": capability,
        "model": model,
        "detail": detail[:160],
        "latency_ms": latency_ms,
        "ts": int(time.time()),
    }
    with _lock:
        _events.append(evt)


def snapshot() -> dict[str, Any]:
    with _lock:
        items = list(_events)
    counts = Counter(e["capability"] for e in items)
    last_by_cap: dict[str, dict] = {}
    for evt in items:
        last_by_cap[evt["capability"]] = evt
    return {
        "capabilities": [
            {
                "name": name,
                "count": counts.get(name, 0),
                "proven": counts.get(name, 0) > 0,
                "last": last_by_cap.get(name),
            }
            for name in _CAPABILITIES
        ],
        "recent": list(reversed(items))[:40],
        "total": len(items),
    }


def reset() -> None:
    with _lock:
        _events.clear()
