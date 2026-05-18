"""
AgriMesh V4.0 — Judge-facing demo + capability map.

Single source of truth for:
  - the 9 Gemma 4 capabilities → AgriMesh code mapping
  - the 6 hackathon track → evidence mapping
  - the 8 scripted /demo scenarios surfaced in Telegram + PWA

Consumed by:
  - app/bot/telegram_bot.py via /demo and /architecture commands
  - app/main.py via GET /api/demo/architecture
"""
from __future__ import annotations

from typing import Any

CAPABILITIES: list[dict[str, str]] = [
    {
        "id": "thinking",
        "name": "Configurable Thinking Mode",
        "code": "app/services/agent.py (thinking_enabled, ReAct planner)",
        "demo": "/demo 6",
    },
    {
        "id": "context",
        "name": "128K Context Window",
        "code": "app/services/memory.py (_compose_full_field_context)",
        "demo": "/demo 3",
    },
    {
        "id": "moe_ple",
        "name": "MoE & PLE Architecture",
        "code": "app/utils/ollama_client.py (gemma4:e4b primary, e2b fallback)",
        "demo": "footer of every reply (model · confidence)",
    },
    {
        "id": "vision",
        "name": "Variable-Resolution Image Budget",
        "code": "app/services/agent.py (photo path) + app/bot/telegram_bot.py (handle_photo)",
        "demo": "/demo 1",
    },
    {
        "id": "audio",
        "name": "Native Audio Processing",
        "code": "app/services/voice.py (Sarvam STT/TTS bridge, dual-script reply)",
        "demo": "/demo 2",
    },
    {
        "id": "pointing",
        "name": "Object Detection & Pointing",
        "code": "app/services/agent.py (photo+pointing) + app/services/verifier.py",
        "demo": "/demo 7",
    },
    {
        "id": "tools",
        "name": "Function Calling",
        "code": "5 MCP servers in app/mcp_servers/* + tool schema in app/services/agent.py",
        "demo": "/demo 5",
    },
    {
        "id": "grammar",
        "name": "Grammar-Constrained Decoding",
        "code": "app/utils/ollama_client.py (USE_GRAMMAR_DECODING) + intent/tool grammars",
        "demo": "/demo 4",
    },
    {
        "id": "multilingual",
        "name": "Multilingual (11 Indic languages)",
        "code": "app/bot/telegram_bot.py (/start picker) + app/services/voice.py auto-detect",
        "demo": "/demo 8",
    },
]

TRACKS: list[dict[str, str]] = [
    {
        "track": "Agriculture / Food Security",
        "evidence": "180-day field memory, NIPHM/PPQS cited IPM, MSP/scheme/insurance from official seed, k-anonymity cluster outbreak alerts",
    },
    {
        "track": "Voice-First / Accessibility",
        "evidence": "11-language /start picker, Sarvam STT auto-detect, dual-script (text+audio) Indic reply, voice in every FSM state",
    },
    {
        "track": "Multimodal AI",
        "evidence": "Photo + voice + text + NDVI + finance fused in one Gemma context; cited IPM PDF excerpt + follow-up loop on each disease photo",
    },
    {
        "track": "Responsible AI / Privacy",
        "evidence": "Device-vs-server data partition, /mydata + /forgetme, k-anonymity ≥3 cluster gate, no chat text via external translate",
    },
    {
        "track": "Edge / Offline-Capable",
        "evidence": "Gemma 4 e2b fallback when e4b OOM, degradation.py: voice→text, mandi→cached MSP, never blank error",
    },
    {
        "track": "Indic Language AI",
        "evidence": "Gemma handles Hindi/Hinglish/Bhojpuri/9 more directly; bilingual *— LANG —* dividers; Sarvam never translates text",
    },
]

# Scripted scenarios. Each /demo N tells the judge exactly what to type
# next so the capability fires on real code, not a recorded fake.
SCENARIOS: list[dict[str, Any]] = [
    {
        "n": 1,
        "title": "Leaf photo → cited IPM card",
        "capability": "Variable-Resolution Image Budget + Vision",
        "feature": "Disease ID with cited NIPHM IPM PDF + follow-up loop opened",
        "type_this": "(send a leaf photo with caption: what's wrong?)",
        "expect": "High-res lesion patch path, disease named, IPM card with non-pyrethroid warning, source link.",
    },
    {
        "n": 2,
        "title": "Hindi voice note → dual-script reply",
        "capability": "Native Audio + Multilingual auto-detect",
        "feature": "Sarvam STT detects language; Gemma answers in same language; TTS returns audio + script",
        "type_this": "(send a voice note in Hindi or Bhojpuri)",
        "expect": "Voice transcript shown, reply rendered in original script AND English, voice clip attached.",
    },
    {
        "n": 3,
        "title": "Long memory recall — last urea dose",
        "capability": "128K Context Window",
        "feature": "180 days observations + 16 wks NDVI + finance fused in one prompt",
        "type_this": "What was my last NPK dose and did it help?",
        "expect": "Quotes the day-30 advisory, links day-78 outcome, references NDVI dip at week 14.",
    },
    {
        "n": 4,
        "title": "MSP lookup with verified citation",
        "capability": "Grammar-Constrained Decoding",
        "feature": "Intent classifier emits one of 8 fixed labels; verifier rejects uncited MSP answers",
        "type_this": "MSP of rice in Patna",
        "expect": "Exact MSP from data/seed/msp_by_state_crop.json plus source line; no hallucinated number.",
    },
    {
        "n": 5,
        "title": "Sell-or-store decision chain",
        "capability": "Function Calling",
        "feature": "Chains mandi → storage → weather MCP servers, returns ROI math",
        "type_this": "Should I sell my rice now or store it?",
        "expect": "Decision (sell/store/wait) with mandi price, storage cost, 7-day rain risk, ROI delta.",
    },
    {
        "n": 6,
        "title": "Cluster intel with k-anonymity",
        "capability": "Configurable Thinking Mode",
        "feature": "ReAct planner aggregates village-scope memory atoms with k≥3 floor",
        "type_this": "Are other farmers in my district seeing brown spot?",
        "expect": "Aggregated answer (count, severity), thinking trace footer, no farmer names ever leak.",
    },
    {
        "n": 7,
        "title": "Pest photo + pointing query",
        "capability": "Object Detection & Pointing",
        "feature": "Spatial pointing on lesion/insect, IPM card with regional warnings",
        "type_this": "(send pest photo, caption: what's on the underside of this leaf?)",
        "expect": "Points at lower stem, IDs brown planthopper, IPM card with non-pyrethroid block.",
    },
    {
        "n": 8,
        "title": "30-day plan in any of 11 languages",
        "capability": "Multilingual + Thinking + Function Calling",
        "feature": "Long context + tool chain composed into a stage-aware plan",
        "type_this": "Plan my next 30 days (try Hindi, Tamil, or Bhojpuri)",
        "expect": "Week-by-week tasks tied to current crop stage, irrigation, mandi window, weather risks.",
    },
]


def architecture_payload() -> dict[str, Any]:
    """Return the full capability map as JSON-serialisable dict (for /api)."""
    return {
        "version": "v4.0",
        "capabilities": CAPABILITIES,
        "tracks": TRACKS,
        "scenarios": SCENARIOS,
        "demo_flow_estimate_minutes": 7,
        "doc": "docs/GEMMA4_CAPABILITY_MAP.md",
    }


def format_demo_index() -> str:
    """Plain-text /demo (no args) menu for Telegram."""
    lines = [
        "*🎬 AgriMesh Judge Demo Menu*",
        "",
        "Each scenario proves one Gemma 4 capability on real code.",
        "Type `/demo N` for the scripted prompt.",
        "",
    ]
    for s in SCENARIOS:
        lines.append(f"`/demo {s['n']}` — {s['title']}")
        lines.append(f"     _{s['capability']}_")
    lines.append("")
    lines.append("Type `/architecture` for the full Gemma 4 → code map.")
    return "\n".join(lines)


def format_scenario(n: int) -> str | None:
    """Format a single /demo N scenario for Telegram, or None if invalid."""
    for s in SCENARIOS:
        if s["n"] == n:
            return (
                f"*🎬 Demo {s['n']} — {s['title']}*\n"
                f"\n"
                f"*Capability:* {s['capability']}\n"
                f"*AgriMesh feature:* {s['feature']}\n"
                f"\n"
                f"*Type this next:*\n"
                f"`{s['type_this']}`\n"
                f"\n"
                f"*You should see:* {s['expect']}\n"
                f"\n"
                f"_This proves Gemma 4 ({s['capability']}) via AgriMesh ({s['feature']})._"
            )
    return None


def format_architecture() -> str:
    """Plain-text /architecture summary for Telegram."""
    lines = [
        "*🏛️ Architecture — How AgriMesh Uses Gemma 4*",
        "",
        "*Gemma 4 capabilities wired in:*",
    ]
    for c in CAPABILITIES:
        lines.append(f"• *{c['name']}* — {c['demo']}")
        lines.append(f"     `{c['code']}`")
    lines.append("")
    lines.append("*Hackathon tracks covered:*")
    for t in TRACKS:
        lines.append(f"• *{t['track']}* — {t['evidence']}")
    lines.append("")
    lines.append("Full map: `docs/GEMMA4_CAPABILITY_MAP.md`")
    lines.append("JSON: `GET /api/demo/architecture`")
    return "\n".join(lines)
