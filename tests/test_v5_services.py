from __future__ import annotations

from datetime import date, timedelta

from app.services.crop_cycle import stage_for_day
from app.services.storage_decision import should_store
from app.services.voice import extract_registration_fields


def test_stage_for_day_uses_playbook_order():
    stage, guidance = stage_for_day("rice", 12)
    assert stage == "nursery_raising"
    assert guidance and guidance["actions"]


def test_storage_decision_recommends_storage_below_msp_near_harvest():
    result = should_store(
        "rice",
        current_price=2000,
        msp=2369,
        district="Munger",
        harvest_date=date.today() - timedelta(days=10),
    )
    assert result["action"] == "STORE"
    assert result["candidate_storages"]


async def test_registration_extraction_falls_back_when_llm_unavailable(monkeypatch):
    def boom():
        raise RuntimeError("ollama down")

    monkeypatch.setattr("app.utils.ollama_client.get_ollama", boom)
    fields = await extract_registration_fields("My name is Ramesh from Munger and I grow paddy")
    assert fields["primary_crop"] == "rice"
    assert fields["name"]
