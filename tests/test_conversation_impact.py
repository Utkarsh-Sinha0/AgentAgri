from __future__ import annotations

from datetime import datetime

from app.models import Advisory, CropCycle, Farmer, Field, Observation
from app.services.conversation import (
    build_action_impact_network,
    build_conversation_context,
    looks_like_followup,
    previous_evidence_article_ids,
    record_turn,
)
from app.utils.security import hash_password


async def test_conversation_turns_preserve_farmer_context(db_session):
    farmer = Farmer(
        id="farmer-conv",
        phone="conv-test",
        hashed_password=hash_password("test"),
        name="Test Farmer",
        district="Munger",
        village="Bariarpur",
    )
    field = Field(id="field-conv", farmer_id=farmer.id, name="East Field", area_acres=1.5)
    cycle = CropCycle(
        id="cycle-conv",
        field_id=field.id,
        crop_name="rice",
        sowing_date=datetime.utcnow(),
        current_stage="vegetative",
        is_active=True,
    )
    db_session.add_all([farmer, field, cycle])
    await db_session.flush()

    await record_turn(
        db_session,
        farmer_id=farmer.id,
        field_id=field.id,
        crop_cycle_id=cycle.id,
        observation_id="obs-1",
        advisory_id="adv-1",
        user_message="मेरे धान में भूरे धब्बे हैं",
        agent_response="Drain field and monitor spots.",
        detected_followup=False,
        risk_level="WATCH",
        confidence="MEDIUM",
        retrieval_path="fast",
        evidence_article_ids=["rice_brown_spot", "water_management_rice"],
        memory_snapshot="field memory",
    )
    await db_session.commit()

    context = await build_conversation_context(db_session, farmer.id, field.id, cycle.id)
    article_ids = await previous_evidence_article_ids(db_session, farmer.id, field.id, cycle.id)

    assert "Conversation continuity" in context
    assert "भूरे धब्बे" in context
    assert article_ids == ["rice_brown_spot", "water_management_rice"]
    assert looks_like_followup("अब क्या करूं?")


async def test_action_impact_network_is_deterministic(db_session):
    farmer = Farmer(
        id="farmer-impact",
        phone="impact-test",
        hashed_password=hash_password("test"),
        name="Impact Farmer",
        district="Munger",
    )
    observation = Observation(
        id="obs-impact",
        farmer_id=farmer.id,
        crop_cycle_id="cycle-impact",
        observation_type="text",
        text_content="leaf spots",
    )
    advisory = Advisory(
        id="adv-impact",
        observation_id=observation.id,
        farmer_id=farmer.id,
        risk_level="PREVENTIVE_ACTION",
        confidence="MEDIUM",
        selected_action_indices=[0],
        selected_warning_indices=[],
        actions_text=["Apply fungicide only after confirming label dose and no rain forecast"],
        warnings_text=[],
        contextualization="Fungal risk is increasing.",
        evidence_article_ids=["rice_blast"],
    )
    db_session.add_all([farmer, observation, advisory])
    await db_session.commit()

    first = await build_action_impact_network(db_session, advisory.id)
    await db_session.commit()
    second = await build_action_impact_network(db_session, advisory.id)

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].impact_level == "high"
    assert "label-approved product" in first[0].dependencies
