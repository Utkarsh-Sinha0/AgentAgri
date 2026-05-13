from __future__ import annotations

from app.models_memory import FarmerProfile
from app.services.demo_seed import seed_demo_memory_palace
from app.services.farmer_dashboard import get_farmer_dashboard, upsert_farmer_profile


async def test_farmer_dashboard_payload_has_profile_weather_map_and_sync(db_session):
    seed = await seed_demo_memory_palace(db_session, telegram_user_id="dashboard-test")

    data = await get_farmer_dashboard(db_session, farmer_id=seed["farmer_id"])

    assert data["farmer"]["id"] == seed["farmer_id"]
    assert data["fields"][0]["id"] == seed["field_id"]
    assert data["fields"][0]["active_crop"]["crop_name"] == seed["crop_name"]
    assert data["weather"]["forecast"]
    assert data["weather_skin"]["mood"]
    assert data["clusters"]["clusters"]
    assert data["finance"]["entries"] >= 5
    assert data["sync"]["offline_ready"] is True
    assert len(data["profile_questions"]) >= 8


async def test_farmer_profile_upsert_updates_completeness(db_session):
    seed = await seed_demo_memory_palace(db_session, telegram_user_id="profile-test")

    result = await upsert_farmer_profile(
        db_session,
        seed["farmer_id"],
        {
            "farm_size_acres": 3.5,
            "irrigation_source": "borewell",
            "water_reliability": "seasonal",
            "soil_test_status": "done_old",
            "annual_budget_rs": 55000,
            "risk_tolerance": "medium",
            "preferred_mandis": "Munger, Bhagalpur",
            "equipment_access": ["pump", "sprayer"],
            "credit_access": "kcc",
            "insurance_status": "pmfby_enrolled",
        },
    )

    assert result["profile"]["farm_size_acres"] == 3.5
    assert result["profile"]["farmer_id"] == seed["farmer_id"]
    assert result["profile"]["preferred_mandis"] == ["Munger", "Bhagalpur"]
    assert result["profile"]["profile_completeness"] >= 0.8
    saved = await db_session.get(FarmerProfile, result["profile"]["id"])
    assert saved is not None
    assert saved.irrigation_source == "borewell"
