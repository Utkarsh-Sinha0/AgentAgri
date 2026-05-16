from __future__ import annotations

from app.models import Advisory, CropCycle, Farmer, Field, Observation
from app.models_memory import ActionImpact
from app.services.conversation import (
    build_action_impact_network,
    build_conversation_context,
    previous_evidence_article_ids,
    record_turn,
)
from app.utils.security import hash_password
from app.utils.time import utc_now


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
        sowing_date=utc_now(),
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


async def test_action_impact_uses_persisted_global_action_index(db_session):
    """Regression: ActionImpact.action_index must mirror selected_action_indices,
    not the display position. Earlier code used enumerate(actions_text) and
    therefore always stored 0..N-1, losing the link to the originating wiki
    action.
    """
    farmer = Farmer(
        id="farmer-impact-idx",
        phone="impact-idx-test",
        hashed_password=hash_password("test"),
        name="Impact Idx Farmer",
        district="Munger",
    )
    observation = Observation(
        id="obs-impact-idx",
        farmer_id=farmer.id,
        crop_cycle_id="cycle-impact-idx",
        observation_type="text",
        text_content="leaf spots",
    )
    advisory = Advisory(
        id="adv-impact-idx",
        observation_id=observation.id,
        farmer_id=farmer.id,
        risk_level="WATCH",
        confidence="MEDIUM",
        # The LLM picked global indices 2 and 5 from the wiki action pool.
        selected_action_indices=[2, 5],
        selected_warning_indices=[],
        actions_text=["First action", "Second action"],
        warnings_text=[],
        contextualization="Watch closely.",
        evidence_article_ids=["rice_blast"],
    )
    db_session.add_all([farmer, observation, advisory])
    await db_session.commit()

    impacts = await build_action_impact_network(db_session, advisory.id)
    await db_session.commit()

    # Sorted by action_text to be deterministic regardless of dict iteration.
    by_text = {imp.action_text: imp for imp in impacts}
    assert by_text["First action"].action_index == 2
    assert by_text["Second action"].action_index == 5


async def test_action_impact_inherits_field_and_crop_scope(db_session):
    """Regression for codex MEDIUM #8: ActionImpact.field_id and
    crop_cycle_id were always None even when the originating observation
    carried both, breaking per-field dashboard filters. The builder must
    copy scope from the advisory's observation.
    """
    farmer = Farmer(
        id="farmer-scope",
        phone="scope-test",
        hashed_password=hash_password("test"),
        name="Scope Farmer",
        district="Munger",
    )
    field = Field(id="field-scope", farmer_id=farmer.id, name="N1", area_acres=1.0)
    cycle = CropCycle(
        id="cycle-scope",
        field_id=field.id,
        crop_name="rice",
        sowing_date=utc_now(),
        current_stage="vegetative",
        is_active=True,
    )
    observation = Observation(
        id="obs-scope",
        farmer_id=farmer.id,
        field_id=field.id,
        crop_cycle_id=cycle.id,
        observation_type="text",
        text_content="leaf spots",
    )
    advisory = Advisory(
        id="adv-scope",
        observation_id=observation.id,
        farmer_id=farmer.id,
        risk_level="WATCH",
        confidence="MEDIUM",
        selected_action_indices=[0],
        selected_warning_indices=[],
        actions_text=["Scout the field weekly"],
        warnings_text=[],
        contextualization="Watch closely.",
        evidence_article_ids=[],
    )
    db_session.add_all([farmer, field, cycle, observation, advisory])
    await db_session.commit()

    impacts = await build_action_impact_network(db_session, advisory.id)
    await db_session.commit()

    assert len(impacts) == 1
    assert impacts[0].field_id == "field-scope"
    assert impacts[0].crop_cycle_id == "cycle-scope"
