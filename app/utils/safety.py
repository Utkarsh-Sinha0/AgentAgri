"""
AgriMesh V4.0 — Safety Filter (Two-Stage)
Stage 1: Regex pre-filter (28 patterns, ~1 ms)
Stage 2: LLM self-grading classifier (~200 ms)

Also exports the XLM-RoBERTa classifier stub for future use.
"""
from __future__ import annotations

import re

from loguru import logger

# ─── Stage 1: Regex Patterns (English + Hindi) ────────────────────────

ENGLISH_PATTERNS: list[tuple[str, str]] = [
    # Chemical dosage (dangerous — we never give specific doses)
    (r"\bapply\s+\d+\s*(ml|mL|millilitre|milliliter|litre|liter|L)\b", "chemical_dosage"),
    (r"\bspray\s+\d+\s*(ml|mL|millilitre|milliliter|litre|liter|L)\b", "chemical_dosage"),
    (r"\b\d+\s*(ml|mL)\s+(per|in|into)\s+\d+\s*(L|litre|liter)\b", "chemical_dosage"),
    (r"\bdose\s*[:=]\s*\d+", "chemical_dosage"),
    (r"\b\d+\s*(grams?|gm|g|kg)\s+per\s+(acre|hectare|bigha)\b", "chemical_dosage"),

    # Medical guarantees
    (r"\b(100%|hundred percent|certainly|cured?)\s+(cure|fix|solve|heal)\b", "medical_guarantee"),
    (r"\b(guarantee[d]?|assured?|promise[d]?)\s+(to\s+)?(cure|yield|harvest)\b", "medical_guarantee"),
    (r"\byour\s+crop\s+will\s+(definitely|certainly|surely)\b", "medical_guarantee"),

    # Scheme enrollment promises
    (r"\byou\s+will\s+(get|receive|be\s+given)\s+(Rs\.?|₹)\s*\d+\b", "scheme_promise"),
    (r"\bguaranteed\s+(subsidy|payment|compensation|loan)\b", "scheme_promise"),
    (r"\bPM-KISAN\s+(payment|installment)\s+(will|shall)\s+(come|arrive|be\s+credited)\b", "scheme_promise"),

    # Unsafe practices
    (r"\bmix\s+(any|all|whatever)\s+(pesticide|insecticide|fungicide|herbicide)s?\b", "unsafe_practice"),
    (r"\bapply\s+(pesticide|insecticide)\s+(during|at)\s+(flowering|harvest)\b", "unsafe_practice"),
    (r"\bno\s+need\s+to\s+wear\s+(gloves|mask|protection)\b", "unsafe_practice"),
    (r"\bdrink\s+(pesticide|insecticide|chemical|poison)\b", "unsafe_practice"),
]

# Hindi/Bhojpuri patterns (critical morphological variations covered)
HINDI_PATTERNS: list[tuple[str, str]] = [
    # Chemical dosage
    (r"\d+\s*(मिली|एमएल|लीटर|ml|mL)\s*(प्रति|में|का|का छिड़काव)", "chemical_dosage"),
    (r"कीटनाशकों?\s*(का|की)\s*(मात्रा|खुराक)\s*\d+", "chemical_dosage"),
    (r"\d+\s*(ग्राम|किलो|gm|kg)\s*(प्रति|per)\s*(एकड़|बीघा|हेक्टेयर)", "chemical_dosage"),

    # Medical guarantees
    (r"(100|शत\s*प्रतिशत|पूरी\s*तरह)\s*(ठीक|इलाज|समाधान)", "medical_guarantee"),
    (r"(गारंटी|पक्का|वादा)\s*(है|से|करते)", "medical_guarantee"),
    (r"फसल\s+(पूरी\s*तरह|बिल्कुल)\s*(ठीक|बच|सही)\s*(हो|जाएगी)", "medical_guarantee"),

    # Scheme promises
    (r"(₹|रुपये|रुपए)\s*\d+\s*(मिलेंगे|मिलेगा|आएंगे|आएगा)", "scheme_promise"),
    (r"सब्सिडी\s+(पक्की|गारंटीड|फिक्स)", "scheme_promise"),

    # Unsafe practices
    (r"(कोई\s*भी|जो\s*मन\s*करे)\s*(दवा|कीटनाशक|स्प्रे)\s*(डाल|मिला|कर)", "unsafe_practice"),
    (r"(बिना|ना)\s*(दस्ताने|मास्क)\s*(के|पहने)\s*(स्प्रे|छिड़काव)", "unsafe_practice"),
    (r"फूल\s*(आने|के\s*समय|में)\s*(पर|में)\s*(स्प्रे|दवा|कीटनाशक)", "unsafe_practice"),
]


def _compile_patterns(patterns: list[tuple[str, str]]) -> list[tuple[re.Pattern, str]]:
    return [(re.compile(p, re.IGNORECASE), cat) for p, cat in patterns]

COMPILED_EN = _compile_patterns(ENGLISH_PATTERNS)
COMPILED_HI = _compile_patterns(HINDI_PATTERNS)


# ─── Stage 2: LLM Safety Checker (via Ollama) ─────────────────────────

_ollama_client = None

def _get_ollama():
    global _ollama_client
    if _ollama_client is None:
        from app.utils.ollama_client import get_ollama
        _ollama_client = get_ollama()
    return _ollama_client


async def check_safety_regex(text: str) -> dict:
    """
    Stage 1: Regex pre-filter.
    Returns: {passes: bool, matches: list[dict]}
    """
    matches = []
    for pattern, category in COMPILED_EN:
        for m in pattern.finditer(text):
            matches.append({"category": category, "matched": m.group(), "position": m.start()})
    for pattern, category in COMPILED_HI:
        for m in pattern.finditer(text):
            matches.append({"category": category, "matched": m.group(), "position": m.start()})

    return {"passes": len(matches) == 0, "matches": matches}


async def check_safety_llm(text: str) -> dict:
    """
    Stage 2: LLM self-grading safety classifier.
    Uses Gemma 4 with safety_check schema (grammar-constrained).
    """
    try:
        client = _get_ollama()
        result = await client.safety_check(text)
        return result["parsed"]
    except Exception as exc:
        logger.error(f"LLM safety check failed: {exc}")
        # Fail safe — flag as potentially dangerous
        return {"is_dangerous": True, "danger_category": "other", "reason": f"LLM safety check error: {exc}"}


async def full_safety_check(text: str) -> dict:
    """
    Two-stage safety check:
    1. Regex pre-filter (fast, catches obvious issues)
    2. LLM classifier (catches novel phrasings regex misses)

    Returns: {passes_all: bool, stage1: dict, stage2: dict}
    """
    stage1 = await check_safety_regex(text)

    # If regex already found issues, still run LLM for audit trail but mark as failing
    stage2 = await check_safety_llm(text)

    stage1_ok = stage1["passes"]
    stage2_ok = not stage2.get("is_dangerous", False)

    return {
        "passes_all": stage1_ok and stage2_ok,
        "stage1_regex": stage1,
        "stage2_llm": stage2,
    }


# ─── Safe fallback template ──────────────────────────────────────────

SAFE_FALLBACK_EN = (
    "🌾 Based on available evidence, here are conservative preventive steps:\n\n"
    "1. Monitor your crop daily for any changes in symptoms.\n"
    "2. Maintain proper field sanitation — remove and destroy infected plant parts.\n"
    "3. Ensure good drainage and avoid waterlogging.\n"
    "4. Contact your local Krishi Vigyan Kendra (KVK) or extension worker for in-person diagnosis.\n"
    "5. Do NOT apply any chemical without consulting an expert. Always follow label instructions.\n\n"
    "📞 For urgent help, call Kisan Call Center: 1800-180-1551"
)

SAFE_FALLBACK_HI = (
    "🌾 उपलब्ध साक्ष्यों के आधार पर, यहां कुछ सतर्कतापूर्ण कदम दिए गए हैं:\n\n"
    "1. अपनी फसल की रोज़ाना निगरानी करें और लक्षणों में किसी भी बदलाव पर ध्यान दें।\n"
    "2. खेत की साफ़-सफ़ाई बनाए रखें — संक्रमित पौधों के हिस्सों को हटाकर नष्ट करें।\n"
    "3. अच्छी जल निकासी सुनिश्चित करें और जलभराव से बचें।\n"
    "4. व्यक्तिगत निदान के लिए अपने नजदीकी कृषि विज्ञान केंद्र (KVK) या विस्तार कार्यकर्ता से संपर्क करें।\n"
    "5. बिना विशेषज्ञ की सलाह के कोई भी रसायन न लगाएं। हमेशा लेबल के निर्देशों का पालन करें।\n\n"
    "📞 तत्काल सहायता के लिए, किसान कॉल सेंटर पर कॉल करें: 1800-180-1551"
)
