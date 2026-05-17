"""Cold-storage recommendation rules."""
from __future__ import annotations

from datetime import date, datetime

from app.services import universal_kb


def _days_since(value: str | date | datetime | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        d = value.date()
    elif isinstance(value, date):
        d = value
    else:
        try:
            d = datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return (date.today() - d).days


def should_store(
    crop: str,
    current_price: float | int | None,
    msp: float | int | None,
    *,
    district: str | None = None,
    state: str | None = None,
    insurance_claim_window_open: bool = False,
    harvest_date: str | date | datetime | None = None,
) -> dict:
    storages = universal_kb.get_cold_storage(district=district, state=state, crop=crop)
    days = _days_since(harvest_date)
    below_msp = bool(current_price and msp and float(current_price) < float(msp) * 0.95)
    fresh_harvest = days is None or days <= 30
    if below_msp and fresh_harvest and storages:
        action = "STORE"
        reason = "Market price is below 95% of MSP and nearby storage exists."
    elif insurance_claim_window_open and fresh_harvest:
        action = "WAIT_FOR_CLAIM"
        reason = "Harvest is recent and insurance window is open; confirm claim status before distress sale."
    else:
        action = "SELL_OR_PROCURE"
        reason = "Storage rule did not beat sale/procurement based on current price, MSP, and harvest timing."
    return {
        "action": action,
        "reasoning": reason,
        "candidate_storages": storages[:3],
        "days_since_harvest": days,
        "below_95pct_msp": below_msp,
    }
