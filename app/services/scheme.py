"""
AgriMesh V4.0 — Government Scheme Matching Service
Logic for PM-KISAN, PMFBY, KCC, SHC eligibility.
Seeded data with real API shape.
"""
from __future__ import annotations

from app.utils.time import utc_now

# ─── Scheme Definitions ───────────────────────────────────────────────

SCHEMES = [
    {
        "id": "pm_kisan",
        "name": "PM-KISAN",
        "name_hi": "प्रधानमंत्री किसान सम्मान निधि",
        "description": "₹6,000 per year in 3 installments for small & marginal farmers",
        "description_hi": "छोटे और सीमांत किसानों के लिए ₹6,000 प्रति वर्ष (3 किस्तों में)",
        "eligibility": {
            "land_owned_acres_max": 5.0,
            "excluded_professions": ["income_tax_payer", "govt_employee", "constitutional_post"],
            "farmer_type": ["small", "marginal"],
        },
        "benefit": "₹2,000 per installment (3 installments/year)",
        "apply_link": "https://pmkisan.gov.in",
    },
    {
        "id": "pmfby",
        "name": "PM Fasal Bima Yojana",
        "name_hi": "प्रधानमंत्री फसल बीमा योजना",
        "description": "Crop insurance at very low premium (2% for Kharif, 1.5% for Rabi)",
        "description_hi": "बहुत कम प्रीमियम पर फसल बीमा (खरीफ के लिए 2%, रबी के लिए 1.5%)",
        "eligibility": {
            "crops_covered": ["rice", "wheat", "maize", "pulses", "oilseeds", "cotton", "sugarcane"],
            "loanee_farmer": True,
            "non_loanee_farmer": True,
        },
        "benefit": "Full sum insured for crop loss due to natural calamities",
        "apply_link": "https://pmfby.gov.in",
    },
    {
        "id": "kcc",
        "name": "Kisan Credit Card",
        "name_hi": "किसान क्रेडिट कार्ड",
        "description": "Low-interest crop loan up to ₹3 lakh at 4% effective rate",
        "description_hi": "4% प्रभावी दर पर ₹3 लाख तक का फसल ऋण",
        "eligibility": {
            "all_farmers": True,
            "purpose": ["crop_production", "post_harvest", "consumption_needs"],
        },
        "benefit": "₹3 lakh limit, 4% effective interest (7% with 3% prompt repayment subsidy)",
        "apply_link": "https://www.myscheme.gov.in/schemes/kcc",
    },
    {
        "id": "shc",
        "name": "Soil Health Card",
        "name_hi": "मृदा स्वास्थ्य कार्ड",
        "description": "Free soil testing and personalized fertilizer recommendations",
        "description_hi": "मुफ्त मिट्टी परीक्षण और व्यक्तिगत उर्वरक सिफारिशें",
        "eligibility": {
            "all_farmers": True,
        },
        "benefit": "Soil health card with nutrient status and crop-wise fertilizer advice",
        "apply_link": "https://soilhealth.dac.gov.in",
    },
    {
        "id": "pkvy",
        "name": "Paramparagat Krishi Vikas Yojana",
        "name_hi": "परंपरागत कृषि विकास योजना",
        "description": "Organic farming promotion with ₹50,000 per hectare over 3 years",
        "description_hi": "3 वर्षों में ₹50,000 प्रति हेक्टेयर के साथ जैविक खेती को बढ़ावा",
        "eligibility": {
            "organic_farming": True,
            "min_cluster_size": 50,  # acres
        },
        "benefit": "₹50,000/ha over 3 years for organic inputs, certification, marketing",
        "apply_link": "https://pgsindia-ncof.gov.in",
    },
]


async def match_schemes(
    farmer_profile: dict | None = None,
    field: dict | None = None,
    crop: str | None = None,
) -> dict:
    """
    Match farmer to eligible government schemes.

    Args:
        farmer_profile: {land_owned_acres, farmer_type, state, district, ...}
        field: {area_acres, soil_type, ...}
        crop: current crop name

    Returns:
        dict with matched schemes and eligibility details.
    """
    if farmer_profile is None:
        farmer_profile = {}
    if field is None:
        field = {}

    matched = []
    for scheme in SCHEMES:
        eligibility = scheme.get("eligibility", {})
        is_eligible, reason = _check_eligibility(scheme["id"], eligibility, farmer_profile, field, crop)
        matched.append({
            "scheme_id": scheme["id"],
            "scheme_name": scheme["name"],
            "scheme_name_hi": scheme.get("name_hi", ""),
            "description": scheme["description"],
            "description_hi": scheme.get("description_hi", ""),
            "is_eligible": is_eligible,
            "reason": reason,
            "benefit": scheme["benefit"],
            "apply_link": scheme["apply_link"],
        })

    return {
        "farmer_profile_used": {k: v for k, v in farmer_profile.items() if k != "hashed_password"},
        "total_schemes": len(matched),
        "eligible_count": sum(1 for m in matched if m["is_eligible"]),
        "schemes": matched,
        "source": "seeded — PM-KISAN/PMFBY/KCC/SHC official eligibility criteria",
        "note": "Actual eligibility verified by local agriculture department",
        "generated_at": utc_now().isoformat(),
    }


def _check_eligibility(
    scheme_id: str,
    eligibility: dict,
    farmer: dict,
    field: dict,
    crop: str | None,
) -> tuple[bool, str]:
    """Check if farmer meets scheme eligibility criteria."""

    if scheme_id == "pm_kisan":
        land = farmer.get("land_owned_acres", field.get("area_acres", 999))
        if isinstance(land, int | float) and land > eligibility.get("land_owned_acres_max", 5):
            return False, f"Land holding ({land} acres) exceeds PM-KISAN limit (5 acres)"
        if land <= 0:
            return False, "No land holding data"
        return True, "Small/marginal farmer with <5 acres land"

    if scheme_id == "pmfby":
        if crop and crop.lower() in [c.lower() for c in eligibility.get("crops_covered", [])]:
            return True, f"{crop.title()} is covered under PMFBY"
        if crop:
            return False, f"{crop} is not in the standard PMFBY crop list for your state"
        return True, "Check with local agriculture office for crop coverage"

    if scheme_id == "kcc":
        return True, "All farmers eligible for KCC"

    if scheme_id == "shc":
        return True, "All farmers eligible for Soil Health Card"

    if scheme_id == "pkvy":
        if not farmer.get("organic_farming", False):
            return False, "PKVY is for organic farming clusters"
        return True, "Organic farmer eligible for PKVY"

    return False, "Unable to verify eligibility automatically — consult extension worker"
