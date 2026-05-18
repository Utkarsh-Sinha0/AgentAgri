"""Best-effort web search fallback.

When KB retrieval + MCP tools give us no evidence for a query, we try
DuckDuckGo's HTML endpoint, pull the top result snippets, and hand them
to the caller. Failures here are non-fatal — the caller falls back to a
graceful degradation message.
"""
from __future__ import annotations

import asyncio
import re
from html import unescape
from urllib.parse import unquote, urlparse, parse_qs

import httpx
from loguru import logger


_DDG_URL = "https://html.duckduckgo.com/html/"
_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'.*?<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip(html: str) -> str:
    return unescape(_TAG_RE.sub("", html)).strip()


def _resolve_url(href: str) -> str:
    """DuckDuckGo wraps results in /l/?uddg=<encoded url>."""
    try:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    except Exception:
        pass
    return href


async def web_search(query: str, *, max_results: int = 3, timeout_s: float = 5.0) -> list[dict]:
    """Return up to ``max_results`` {title, url, snippet} dicts. [] on failure."""
    try:
        async with httpx.AsyncClient(
            timeout=timeout_s,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                )
            },
            follow_redirects=True,
        ) as client:
            resp = await client.post(_DDG_URL, data={"q": query, "kl": "in-en"})
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        logger.warning(f"web_search fetch failed for {query!r}: {exc}")
        return []

    results: list[dict] = []
    for m in _RESULT_RE.finditer(html):
        title = _strip(m.group("title"))
        snippet = _strip(m.group("snippet"))
        url = _resolve_url(m.group("href"))
        if title and snippet:
            results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    if not results:
        logger.warning(f"web_search returned 0 parsed results for {query!r}")
    return results


_GRACEFUL_NATIVE = {
    "hi": (
        "🙏 क्षमा करें — इस सवाल का सटीक उत्तर देने के लिए मेरे पास अभी "
        "पर्याप्त सत्यापित डेटा नहीं है।\nमैं अभी विकास चरण में हूँ — डेवलपर "
        "जल्द ही रियल-टाइम वेब क्रॉलर जोड़ेंगे।"
    ),
    "bn": (
        "🙏 দুঃখিত — এই প্রশ্নের সঠিক উত্তর দেওয়ার মতো যাচাইকৃত তথ্য আমার "
        "কাছে নেই।\nআমি এখনও উন্নয়ন পর্যায়ে আছি — ডেভেলপার শীঘ্রই রিয়েল-টাইম "
        "ওয়েব ক্রলার যুক্ত করবেন।"
    ),
    "ta": (
        "🙏 மன்னிக்கவும் — இந்த கேள்விக்கு துல்லியமாக பதிலளிக்க தேவையான "
        "சரிபார்க்கப்பட்ட தரவு என்னிடம் இல்லை.\nநான் இன்னும் வளர்ச்சி கட்டத்தில் "
        "உள்ளேன் — டெவலப்பர் விரைவில் ரியல்-டைம் வலை க்ராலரை இணைப்பார்."
    ),
    "te": (
        "🙏 క్షమించండి — ఈ ప్రశ్నకు ఖచ్చితంగా సమాధానం ఇవ్వడానికి సరిపోయే ధృవీకృత "
        "డేటా నా దగ్గర లేదు.\nనేను ఇంకా అభివృద్ధి దశలో ఉన్నాను — డెవలపర్ "
        "త్వరలో రియల్-టైమ్ వెబ్ క్రాలర్‌ను జోడిస్తాడు."
    ),
    "mr": (
        "🙏 क्षमस्व — या प्रश्नाचे अचूक उत्तर देण्यासाठी पुरेसा सत्यापित डेटा "
        "माझ्याकडे नाही.\nमी अजून विकास टप्प्यात आहे — डेव्हलपर लवकरच रिअल-टाइम "
        "वेब क्रॉलर जोडतील."
    ),
    "gu": (
        "🙏 માફ કરશો — આ પ્રશ્નનો સચોટ જવાબ આપવા માટે પૂરતો ચકાસાયેલ ડેટા મારી "
        "પાસે નથી.\nહું હજુ વિકાસ તબક્કામાં છું — ડેવલપર ટૂંક સમયમાં રિયલ-ટાઇમ "
        "વેબ ક્રોલર ઉમેરશે."
    ),
    "kn": (
        "🙏 ಕ್ಷಮಿಸಿ — ಈ ಪ್ರಶ್ನೆಗೆ ನಿಖರವಾಗಿ ಉತ್ತರಿಸಲು ಸಾಕಷ್ಟು ಪರಿಶೀಲಿತ ಡೇಟಾ "
        "ನನ್ನ ಬಳಿ ಇಲ್ಲ.\nನಾನು ಇನ್ನೂ ಅಭಿವೃದ್ಧಿ ಹಂತದಲ್ಲಿದ್ದೇನೆ — ಡೆವಲಪರ್ ಶೀಘ್ರದಲ್ಲೇ "
        "ರಿಯಲ್-ಟೈಮ್ ವೆಬ್ ಕ್ರಾಲರ್ ಸೇರಿಸುತ್ತಾರೆ."
    ),
    "ml": (
        "🙏 ക്ഷമിക്കണം — ഈ ചോദ്യത്തിന് കൃത്യമായ ഉത്തരം നൽകാൻ വേണ്ടത്ര "
        "സ്ഥിരീകരിച്ച ഡാറ്റ എനിക്കില്ല.\nഞാൻ ഇപ്പോഴും വികസന ഘട്ടത്തിലാണ് — "
        "ഡെവലപ്പർ ഉടൻ റിയൽ-ടൈം വെബ് ക്രോളർ ചേർക്കും."
    ),
    "pa": (
        "🙏 ਮਾਫ਼ ਕਰਨਾ — ਇਸ ਸਵਾਲ ਦਾ ਸਹੀ ਜਵਾਬ ਦੇਣ ਲਈ ਮੇਰੇ ਕੋਲ ਕਾਫ਼ੀ ਪ੍ਰਮਾਣਿਤ "
        "ਡਾਟਾ ਨਹੀਂ ਹੈ।\nਮੈਂ ਅਜੇ ਵਿਕਾਸ ਪੜਾਅ ਵਿੱਚ ਹਾਂ — ਡਿਵੈਲਪਰ ਛੇਤੀ ਹੀ "
        "ਰੀਅਲ-ਟਾਈਮ ਵੈੱਬ ਕ੍ਰਾਲਰ ਜੋੜਨਗੇ।"
    ),
    "or": (
        "🙏 କ୍ଷମା କରନ୍ତୁ — ଏହି ପ୍ରଶ୍ନର ସଠିକ୍ ଉତ୍ତର ଦେବାକୁ ଯଥେଷ୍ଟ ଯାଞ୍ଚିତ ତଥ୍ୟ "
        "ମୋ ପାଖରେ ନାହିଁ।\nମୁଁ ଏବେ ବି ବିକାଶ ପର୍ଯ୍ୟାୟରେ ଅଛି — ଡେଭଲପର୍ ଶୀଘ୍ର "
        "ରିଅଲ୍-ଟାଇମ୍ ୱେବ୍ କ୍ରଲର୍ ଯୋଡ଼ିବେ।"
    ),
    "ur": (
        "🙏 معذرت — اس سوال کا درست جواب دینے کے لیے میرے پاس کافی تصدیق شدہ "
        "ڈیٹا نہیں ہے۔\nمیں ابھی ترقی کے مرحلے میں ہوں — ڈویلپر جلد ہی "
        "ریئل ٹائم ویب کرالر شامل کریں گے۔"
    ),
}

_GRACEFUL_EN = (
    "🙏 Sorry — I don't have enough verified data in my universal knowledge "
    "to answer this confidently.\n"
    "I'm still in development. My developer will integrate a real-time web "
    "crawler to extract such data accurately."
)

_HELPLINE = "📞 Kisan Call Center: 1800-180-1551"


def graceful_unknown_message(language: str | None) -> str:
    """Bilingual graceful degradation in the farmer's chosen Indic + English."""
    code = (language or "").strip().lower()[:2]
    native = _GRACEFUL_NATIVE.get(code)
    if code == "en" or not native:
        return f"{_GRACEFUL_EN}\n\n{_HELPLINE}"
    return f"{native}\n\n{_GRACEFUL_EN}\n\n{_HELPLINE}"
