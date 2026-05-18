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

# Hindi translations of scenario user-facing fields, indexed by scenario N.
# English in SCENARIOS is canonical; we render bilingual for Indic langs and
# English-only for code "en". Other Indic langs reuse the Hindi rendering as
# the second-script fallback (same pattern as WELCOME_TEXTS in telegram_bot).
SCENARIOS_HI: dict[int, dict[str, str]] = {
    1: {
        "title": "पत्ती की फोटो → स्रोत-सहित IPM कार्ड",
        "capability": "Variable-Resolution Image Budget + Vision",
        "feature": "रोग पहचान + NIPHM IPM PDF का हवाला + follow-up loop खुलता है",
        "type_this": "(पत्ती की फोटो भेजें, caption: क्या बीमारी है?)",
        "expect": "उच्च-रिज़ोल्यूशन lesion patch path, बीमारी का नाम, IPM कार्ड (non-pyrethroid चेतावनी सहित), स्रोत लिंक।",
    },
    2: {
        "title": "हिंदी voice note → dual-script जवाब",
        "capability": "Native Audio + Multilingual auto-detect",
        "feature": "Sarvam STT भाषा पहचानता है; Gemma उसी भाषा में जवाब; TTS audio + script दोनों",
        "type_this": "(हिंदी या भोजपुरी में voice note भेजें)",
        "expect": "Voice transcript दिखे, जवाब अपनी लिपि + English दोनों में, voice clip attached।",
    },
    3: {
        "title": "लंबी memory recall — पिछली urea खुराक",
        "capability": "128K Context Window",
        "feature": "180 दिन observations + 16 हफ्ते NDVI + finance एक prompt में",
        "type_this": "पिछली NPK खुराक क्या थी और क्या उससे फायदा हुआ?",
        "expect": "Day-30 advisory की quote, day-78 outcome से link, week-14 NDVI dip का reference।",
    },
    4: {
        "title": "स्रोत-सहित MSP lookup",
        "capability": "Grammar-Constrained Decoding",
        "feature": "Intent classifier 8 fixed labels में से एक देता है; verifier बिना citation MSP जवाब reject करता है",
        "type_this": "पटना में चावल का MSP",
        "expect": "data/seed/msp_by_state_crop.json से exact MSP + source line; कोई hallucinated number नहीं।",
    },
    5: {
        "title": "बेचूँ या रखूँ — decision chain",
        "capability": "Function Calling",
        "feature": "mandi → storage → weather MCP servers chain, ROI math",
        "type_this": "मेरा चावल अभी बेचूँ या रखूँ?",
        "expect": "निर्णय (बेचो/रखो/रुको) + मंडी भाव + storage cost + 7-दिन बारिश risk + ROI delta।",
    },
    6: {
        "title": "Cluster intel (k-anonymity के साथ)",
        "capability": "Configurable Thinking Mode",
        "feature": "ReAct planner village-scope memory atoms को k≥3 floor के साथ aggregate करता है",
        "type_this": "क्या मेरे ज़िले के दूसरे किसानों को brown spot दिख रहा है?",
        "expect": "Aggregated जवाब (count, severity), thinking trace footer, कोई farmer name leak नहीं।",
    },
    7: {
        "title": "कीट की फोटो + pointing query",
        "capability": "Object Detection & Pointing",
        "feature": "Spatial pointing on lesion/कीट, IPM card with regional warnings",
        "type_this": "(कीट की फोटो भेजें, caption: पत्ती के नीचे क्या है?)",
        "expect": "तने के निचले हिस्से की ओर इशारा, brown planthopper की पहचान, IPM card (non-pyrethroid block)।",
    },
    8: {
        "title": "30-दिन की योजना (किसी भी 11 भाषा में)",
        "capability": "Multilingual + Thinking + Function Calling",
        "feature": "Long context + tool chain से stage-aware योजना",
        "type_this": "अगले 30 दिन की योजना बनाओ (हिंदी, तमिल, या भोजपुरी आज़माएँ)",
        "expect": "Week-by-week tasks (current crop stage, irrigation, mandi window, weather risks के साथ)।",
    },
}

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


def _lang_mode(lang: str | None) -> str:
    """Return 'en' (English only), 'hi' (Hindi only), or 'dual' (Hindi+English)."""
    code = (lang or "en").lower()[:2]
    if code == "en":
        return "en"
    if code == "hi":
        return "hi"
    return "dual"


def _scenario_index_en() -> list[str]:
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
    return lines


def _scenario_index_hi() -> list[str]:
    lines = [
        "*🎬 AgriMesh जज डेमो मेन्यू*",
        "",
        "हर scenario एक Gemma 4 capability को real code पर साबित करता है।",
        "Scripted prompt के लिए `/demo N` लिखें।",
        "",
    ]
    for s in SCENARIOS:
        hi = SCENARIOS_HI.get(s["n"], {})
        lines.append(f"`/demo {s['n']}` — {hi.get('title', s['title'])}")
        lines.append(f"     _{hi.get('capability', s['capability'])}_")
    lines.append("")
    lines.append("पूरे Gemma 4 → code map के लिए `/architecture` लिखें।")
    return lines


def format_demo_index(lang: str | None = None) -> str:
    """Plain-text /demo (no args) menu for Telegram, language-aware.

    - 'en'           → English only
    - 'hi'           → Hindi only
    - other Indic    → Hindi block then English block (dual-script)
    """
    mode = _lang_mode(lang)
    if mode == "en":
        return "\n".join(_scenario_index_en())
    if mode == "hi":
        return "\n".join(_scenario_index_hi())
    return "\n".join(_scenario_index_hi() + ["", "────────", ""] + _scenario_index_en())


def _scenario_lines_en(s: dict[str, Any]) -> list[str]:
    return [
        f"*🎬 Demo {s['n']} — {s['title']}*",
        "",
        f"*Capability:* {s['capability']}",
        f"*AgriMesh feature:* {s['feature']}",
        "",
        "*Type this next:*",
        f"`{s['type_this']}`",
        "",
        f"*You should see:* {s['expect']}",
        "",
        f"_This proves Gemma 4 ({s['capability']}) via AgriMesh ({s['feature']})._",
    ]


def _scenario_lines_hi(s: dict[str, Any]) -> list[str]:
    hi = SCENARIOS_HI.get(s["n"], {})
    return [
        f"*🎬 डेमो {s['n']} — {hi.get('title', s['title'])}*",
        "",
        f"*क्षमता:* {hi.get('capability', s['capability'])}",
        f"*AgriMesh feature:* {hi.get('feature', s['feature'])}",
        "",
        "*अब यह लिखें:*",
        f"`{hi.get('type_this', s['type_this'])}`",
        "",
        f"*यह दिखना चाहिए:* {hi.get('expect', s['expect'])}",
        "",
        f"_Gemma 4 ({hi.get('capability', s['capability'])}) को AgriMesh ({hi.get('feature', s['feature'])}) के ज़रिए साबित करता है।_",
    ]


def format_scenario(n: int, lang: str | None = None) -> str | None:
    """Format a single /demo N scenario in the given language, or None if invalid."""
    s = next((x for x in SCENARIOS if x["n"] == n), None)
    if s is None:
        return None
    mode = _lang_mode(lang)
    if mode == "en":
        return "\n".join(_scenario_lines_en(s))
    if mode == "hi":
        return "\n".join(_scenario_lines_hi(s))
    return "\n".join(_scenario_lines_hi(s) + ["", "────────", ""] + _scenario_lines_en(s))


def _architecture_lines_en() -> list[str]:
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
    return lines


def _architecture_lines_hi() -> list[str]:
    lines = [
        "*🏛️ Architecture — AgriMesh में Gemma 4 कैसे चलता है*",
        "",
        "*जुड़ी हुई Gemma 4 capabilities:*",
    ]
    for c in CAPABILITIES:
        lines.append(f"• *{c['name']}* — {c['demo']}")
        lines.append(f"     `{c['code']}`")
    lines.append("")
    lines.append("*Cover किए गए hackathon tracks:*")
    for t in TRACKS:
        lines.append(f"• *{t['track']}* — {t['evidence']}")
    lines.append("")
    lines.append("पूरा map: `docs/GEMMA4_CAPABILITY_MAP.md`")
    lines.append("JSON: `GET /api/demo/architecture`")
    return lines


def format_architecture(lang: str | None = None) -> str:
    """Plain-text /architecture summary for Telegram, language-aware."""
    mode = _lang_mode(lang)
    if mode == "en":
        return "\n".join(_architecture_lines_en())
    if mode == "hi":
        return "\n".join(_architecture_lines_hi())
    return "\n".join(_architecture_lines_hi() + ["", "────────", ""] + _architecture_lines_en())
