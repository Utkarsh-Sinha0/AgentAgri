from __future__ import annotations

from datetime import date, timedelta

from app.services.crop_cycle import stage_for_day
from app.services.storage_decision import should_store
from app.services.universal_kb import get_common_issue_memory, get_reference_manuals, retrieve
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


def test_official_reference_manuals_are_retrievable_by_crop():
    rice_manuals = get_reference_manuals("rice")
    wheat_manuals = get_reference_manuals("wheat")

    assert rice_manuals and rice_manuals[0]["is_official"] is True
    assert wheat_manuals and "AESA" in wheat_manuals[0]["title"]

    docs = retrieve(crop="rice", query="stem borer sustainable control")
    manual_docs = [d for d in docs if d["doc_type"] == "official_manual"]
    assert manual_docs
    assert "chemical control only as a last choice" in " ".join(
        manual_docs[0]["content"]["sustainable_first_rules"]
    )


def test_common_issue_memory_is_sustainable_first_and_weather_aware():
    rows = get_common_issue_memory("rice", query="brown spots on rice leaves after rain")
    assert rows
    card = rows[0]
    assert card["issue"] == "brown spot"
    assert card["non_chemical_first"]
    assert card["weather_factors"]
    assert "Kisan Call Centre" in card["safety_note"]

    docs = retrieve(crop="wheat", query="yellow rust after humid weather")
    common_docs = [d for d in docs if d["doc_type"] == "common_issue_memory"]
    assert common_docs
    assert common_docs[0]["content"]["issue"] == "yellow rust"
