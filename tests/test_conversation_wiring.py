from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import CropCycle, Farmer, Field
from app.models_memory import ConversationThread, ConversationTurn
from app.services.conversation import record_turn
from app.utils.security import hash_password
from app.utils.time import utc_now


async def test_record_turn_honors_explicit_thread_id(db_session):
    farmer = Farmer(
        id="farmer-wiring",
        phone="wiring-test",
        hashed_password=hash_password("test"),
        name="Wiring Farmer",
        district="Munger",
    )
    field = Field(id="field-wiring", farmer_id=farmer.id, name="W", area_acres=1.0)
    cycle = CropCycle(
        id="cycle-wiring",
        field_id=field.id,
        crop_name="rice",
        sowing_date=utc_now(),
        current_stage="vegetative",
        is_active=True,
    )
    db_session.add_all([farmer, field, cycle])
    await db_session.flush()

    older_ts = utc_now() - timedelta(hours=2)
    newer_ts = utc_now()
    older = ConversationThread(
        id="thr-older",
        farmer_id=farmer.id,
        field_id=field.id,
        crop_cycle_id=cycle.id,
        channel="telegram",
        is_active=True,
        updated_at=older_ts,
    )
    newer = ConversationThread(
        id="thr-newer",
        farmer_id=farmer.id,
        field_id=field.id,
        crop_cycle_id=cycle.id,
        channel="telegram",
        is_active=True,
        updated_at=newer_ts,
    )
    db_session.add_all([older, newer])
    await db_session.flush()

    turn = await record_turn(
        db_session,
        farmer_id=farmer.id,
        field_id=field.id,
        crop_cycle_id=cycle.id,
        observation_id=None,
        advisory_id=None,
        user_message="test",
        agent_response="resp",
        detected_followup=False,
        risk_level="WATCH",
        confidence="medium",
        retrieval_path="none",
        evidence_article_ids=[],
        memory_snapshot="",
        thread_id=older.id,
    )

    assert turn.thread_id == older.id

    on_newer = (
        await db_session.execute(
            select(ConversationTurn).where(ConversationTurn.thread_id == newer.id)
        )
    ).scalars().all()
    assert len(on_newer) == 0
