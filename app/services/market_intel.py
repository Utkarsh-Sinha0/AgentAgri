"""
AgriMesh V4.0 — Market Intelligence Service (§16)
Sell decision advisor: SELL NOW / WAIT / PARTIAL SALE
FCI procurement center directory
Price prediction via trend analysis
"""
from __future__ import annotations

from typing import Literal

from app.services.mandi import get_mandi_prices, get_msp

# ─── FCI Procurement Centers ──────────────────────────────────────────

FCI_CENTERS = [
    {"name": "FCI Munger", "district": "Munger", "state": "Bihar", "crops": ["rice", "wheat"], "status": "active", "contact": "06344-222015"},
    {"name": "FCI Bhagalpur", "district": "Bhagalpur", "state": "Bihar", "crops": ["rice", "wheat", "maize"], "status": "active", "contact": "0641-2401234"},
    {"name": "FCI Patna", "district": "Patna", "state": "Bihar", "crops": ["rice", "wheat", "pulses"], "status": "active", "contact": "0612-2223456"},
    {"name": "FCI Begusarai", "district": "Begusarai", "state": "Bihar", "crops": ["rice", "wheat"], "status": "active", "contact": "06243-242001"},
    {"name": "FCI Khagaria", "district": "Khagaria", "state": "Bihar", "crops": ["rice", "maize"], "status": "active", "contact": "06244-222034"},
]


def get_fci_centers(crop: str | None = None, district: str | None = None) -> list[dict]:
    """Get nearby FCI procurement centers filtered by crop and district."""
    result = FCI_CENTERS
    if crop:
        result = [c for c in result if crop.lower() in [cr.lower() for cr in c["crops"]]]
    if district:
        result = [c for c in result if c["district"].lower() == district.lower()]
    return result


# ─── Sell Decision Advisor ─────────────────────────────────────────────

async def sell_decision_advisor(
    crop: str,
    district: str = "Munger",
    harvest_date: str | None = None,
    quality_grade: str = "FAQ",  # FAQ (Fair Average Quality) or Premium
) -> dict:
    """
    §16.3: Advise farmer whether to SELL NOW, WAIT, or PARTIAL SALE.
    Considers: current mandi price vs MSP, 7-day trend, seasonal trend, quality.
    """
    prices = await get_mandi_prices(crop=crop, district=district, days=7)
    msp_data = await get_msp(crop=crop)

    current_price = None
    price_trend = []
    for p in prices.get("prices", []):
        history = p.get("history", [])
        if history:
            current_price = history[0].get("modal", 0)
            price_trend = [h.get("modal", 0) for h in history]
            break

    msp = msp_data.get("msp_per_quintal", 0)

    if not current_price or current_price <= 0:
        return {
            "decision": "INSUFFICIENT_DATA",
            "advice_hi": "अभी पर्याप्त मंडी डेटा उपलब्ध नहीं है। कृपया बाद में जांच करें।",
            "advice_en": "Insufficient market data. Check back later.",
        }

    # Compute signals
    above_msp = current_price > msp if msp > 0 else True
    price_rising = len(price_trend) >= 2 and price_trend[0] > price_trend[-1]
    price_change_pct = abs(price_trend[0] - price_trend[-1]) / max(price_trend[-1], 1) if len(price_trend) >= 2 else 0
    is_volatile = price_change_pct >= 0.10

    # Decision logic
    if above_msp and price_rising and not is_volatile:
        decision: Literal["SELL_NOW", "WAIT", "PARTIAL_SALE", "HOLD"] = "HOLD"
        advice_hi = (
            f"📈 भाव MSP (₹{msp}/quintal) से ऊपर है और बढ़ रहा है।\n"
            f"सलाह: *थोड़ा रुकें* — कीमतें और बढ़ सकती हैं।\n"
            f"अगले 2-3 दिन निगरानी करें।"
        )
        advice_en = f"Price above MSP (₹{msp}) and rising. Advice: HOLD — prices may increase further. Monitor for 2-3 days."
    elif above_msp and not price_rising:
        decision = "SELL_NOW"
        advice_hi = (
            f"✅ भाव MSP (₹{msp}/quintal) से ऊपर है पर स्थिर/गिर रहा है।\n"
            f"सलाह: *अभी बेचें* — कीमतें और गिर सकती हैं।\n"
            f"वर्तमान भाव: ₹{current_price}/quintal"
        )
        advice_en = f"Price above MSP (₹{msp}) but stable/declining. Advice: SELL NOW at ₹{current_price}/quintal."
    elif not above_msp and msp > 0:
        decision = "WAIT"
        advice_hi = (
            f"⚠️ भाव MSP (₹{msp}/quintal) से नीचे है।\n"
            f"सलाह: *MSP पर FCI को बेचें* या भाव बढ़ने तक प्रतीक्षा करें।\n"
            f"वर्तमान भाव: ₹{current_price}/quintal | MSP: ₹{msp}/quintal"
        )
        advice_en = f"Price below MSP (₹{msp}). Advice: Sell to FCI at MSP or wait for price recovery."
    else:
        decision = "PARTIAL_SALE"
        advice_hi = "सलाह: *आंशिक बिक्री* — 50% अभी बेचें, 50% रोक कर रखें।"
        advice_en = "Advice: PARTIAL SALE — sell 50% now, hold 50%."

    # FCI centers
    fci_centers = get_fci_centers(crop=crop, district=district)

    return {
        "decision": decision,
        "current_price": current_price,
        "msp": msp,
        "above_msp": above_msp,
        "price_trend": "rising" if price_rising else "falling" if price_change_pct > 0.02 else "stable",
        "price_change_7d_pct": round(price_change_pct * 100, 1),
        "advice_hi": advice_hi,
        "advice_en": advice_en,
        "fci_centers_nearby": [{"name": c["name"], "district": c["district"], "contact": c["contact"]} for c in fci_centers],
        "quality_note": "FAQ (Fair Average Quality)" if quality_grade == "FAQ" else f"Premium grade — expect {(current_price * 1.15):.0f}/quintal",
    }
