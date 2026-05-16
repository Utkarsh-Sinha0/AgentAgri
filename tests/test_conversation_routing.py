from __future__ import annotations

from sqlalchemy import select

from app.models import CropCycle, Farmer, Field
from app.models_memory import ConversationThread
from app.services.conversation import route_to_thread
from app.utils.security import hash_password
from app.utils.time import utc_now


async def _seed_farmer_with_two_fields(db_session):
    """Two fields, two crop cycles: rice on field-A, tomato on field-B."""
    farmer = Farmer(
        id="farmer-route",
        phone="route-test",
        hashed_password=hash_password("test"),
        name="Route Farmer",
        district="Munger",
    )
    field_a = Field(id="field-A", farmer_id=farmer.id, name="A", area_acres=1.0)
    field_b = Field(id="field-B", farmer_id=farmer.id, name="B", area_acres=1.0)
    cycle_rice = CropCycle(
        id="cycle-rice",
        field_id=field_a.id,
        crop_name="rice",
        sowing_date=utc_now(),
        current_stage="vegetative",
        is_active=True,
    )
    cycle_tomato = CropCycle(
        id="cycle-tomato",
        field_id=field_b.id,
        crop_name="Tomato",
        sowing_date=utc_now(),
        current_stage="vegetative",
        is_active=True,
    )
    db_session.add_all([farmer, field_a, field_b, cycle_rice, cycle_tomato])
    await db_session.flush()
    return farmer, field_a, field_b, cycle_rice, cycle_tomato


async def test_route_returns_exact_scope_thread(db_session):
    """Branch 1: an active thread for the exact (farmer, field, crop, channel)
    scope is returned without creating a new one."""
    farmer, field_a, _, cycle_rice, _ = await _seed_farmer_with_two_fields(db_session)

    seeded = ConversationThread(
        id="thr-exact",
        farmer_id=farmer.id,
        field_id=field_a.id,
        crop_cycle_id=cycle_rice.id,
        channel="telegram",
        title="rice • disease",
        is_active=True,
    )
    db_session.add(seeded)
    await db_session.flush()

    result = await route_to_thread(
        db_session,
        farmer_id=farmer.id,
        field_id=field_a.id,
        crop_cycle_id=cycle_rice.id,
        intent_crop_name="rice",
    )

    assert result.id == "thr-exact"
    count = await db_session.scalar(
        select(ConversationThread.id).where(ConversationThread.farmer_id == farmer.id)
    )
    rows = (
        await db_session.execute(
            select(ConversationThread).where(ConversationThread.farmer_id == farmer.id)
        )
    ).scalars().all()
    assert len(rows) == 1, "exact-scope branch must not create extra threads"
    assert count == "thr-exact"


async def test_route_crosses_scope_when_scope_empty_and_crop_matches(db_session):
    """Branch 2: no thread for the current scope, but the farmer has an active
    thread on a different field whose crop matches intent_crop_name → return it."""
    farmer, field_a, field_b, cycle_rice, cycle_tomato = await _seed_farmer_with_two_fields(
        db_session
    )

    # An active rice thread exists on field-A.
    rice_thread = ConversationThread(
        id="thr-rice",
        farmer_id=farmer.id,
        field_id=field_a.id,
        crop_cycle_id=cycle_rice.id,
        channel="telegram",
        title="rice • disease",
        is_active=True,
    )
    db_session.add(rice_thread)
    await db_session.flush()

    # Farmer sends a "rice" message while the bot's current scope is field-B / tomato.
    result = await route_to_thread(
        db_session,
        farmer_id=farmer.id,
        field_id=field_b.id,
        crop_cycle_id=cycle_tomato.id,
        intent_crop_name="rice",
    )

    assert result.id == "thr-rice"
    # No new thread was created for the (field-B, tomato) scope.
    rows = (
        await db_session.execute(
            select(ConversationThread).where(ConversationThread.farmer_id == farmer.id)
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_route_creates_thread_when_no_match(db_session):
    """Branch 3: no exact-scope thread and crop doesn't match any other thread
    → fall back to get_or_create_thread (creates a new one)."""
    farmer, _, field_b, _, cycle_tomato = await _seed_farmer_with_two_fields(db_session)

    result = await route_to_thread(
        db_session,
        farmer_id=farmer.id,
        field_id=field_b.id,
        crop_cycle_id=cycle_tomato.id,
        intent_crop_name="wheat",  # no wheat thread, no wheat cycle
    )

    assert result.field_id == field_b.id
    assert result.crop_cycle_id == cycle_tomato.id
    assert result.is_active is True


async def test_exact_scope_wins_even_if_other_crop_also_active(db_session):
    """Regression guard: when both an exact-scope thread AND a cross-scope crop
    match exist, exact scope must win. (Avoids accidentally hopping to a
    different field when the farmer is firmly in their current scope.)"""
    farmer, field_a, field_b, cycle_rice, cycle_tomato = await _seed_farmer_with_two_fields(
        db_session
    )
    tomato_thread = ConversationThread(
        id="thr-tomato",
        farmer_id=farmer.id,
        field_id=field_b.id,
        crop_cycle_id=cycle_tomato.id,
        channel="telegram",
        title="tomato • disease",
        is_active=True,
    )
    rice_thread = ConversationThread(
        id="thr-rice",
        farmer_id=farmer.id,
        field_id=field_a.id,
        crop_cycle_id=cycle_rice.id,
        channel="telegram",
        title="rice • disease",
        is_active=True,
    )
    db_session.add_all([tomato_thread, rice_thread])
    await db_session.flush()

    # Farmer is on tomato scope but message extracted crop_name='rice'.
    # Per design §7.2 step 2, cross-scope only triggers when current scope has
    # NO active thread. Here it does → return the tomato thread.
    result = await route_to_thread(
        db_session,
        farmer_id=farmer.id,
        field_id=field_b.id,
        crop_cycle_id=cycle_tomato.id,
        intent_crop_name="rice",
    )
    assert result.id == "thr-tomato"


async def test_archived_threads_are_ignored(db_session):
    """Cross-scope branch must skip is_active=False threads. Otherwise
    /endthread leaves a zombie that re-attaches on the next message."""
    farmer, field_a, field_b, cycle_rice, cycle_tomato = await _seed_farmer_with_two_fields(
        db_session
    )
    archived_rice = ConversationThread(
        id="thr-rice-archived",
        farmer_id=farmer.id,
        field_id=field_a.id,
        crop_cycle_id=cycle_rice.id,
        channel="telegram",
        title="rice • disease",
        is_active=False,
    )
    db_session.add(archived_rice)
    await db_session.flush()

    result = await route_to_thread(
        db_session,
        farmer_id=farmer.id,
        field_id=field_b.id,
        crop_cycle_id=cycle_tomato.id,
        intent_crop_name="rice",
    )

    # Falls through to get_or_create_thread → fresh thread for (field-B, tomato).
    assert result.id != "thr-rice-archived"
    assert result.field_id == field_b.id
    assert result.crop_cycle_id == cycle_tomato.id


async def test_channel_isolation(db_session):
    """A thread on a different channel must not be returned by cross-scope
    routing for telegram."""
    farmer, field_a, field_b, cycle_rice, cycle_tomato = await _seed_farmer_with_two_fields(
        db_session
    )
    web_thread = ConversationThread(
        id="thr-rice-web",
        farmer_id=farmer.id,
        field_id=field_a.id,
        crop_cycle_id=cycle_rice.id,
        channel="web",
        title="rice • disease",
        is_active=True,
    )
    db_session.add(web_thread)
    await db_session.flush()

    result = await route_to_thread(
        db_session,
        farmer_id=farmer.id,
        field_id=field_b.id,
        crop_cycle_id=cycle_tomato.id,
        intent_crop_name="rice",
        channel="telegram",
    )

    # Cross-scope must not pick the web thread; falls through to create a new
    # telegram thread for the current scope.
    assert result.id != "thr-rice-web"
    assert result.channel == "telegram"


async def test_case_insensitive_crop_match(db_session):
    """CropCycle.crop_name may be stored as 'Tomato' while intent gives
    'tomato'. The match must be case-insensitive (intent schema requires
    lowercase English; cycle data is user-entered)."""
    farmer, field_a, field_b, _, cycle_tomato = await _seed_farmer_with_two_fields(
        db_session
    )
    tomato_thread = ConversationThread(
        id="thr-tomato-cap",
        farmer_id=farmer.id,
        field_id=field_b.id,
        crop_cycle_id=cycle_tomato.id,  # crop_name='Tomato' in fixture
        channel="telegram",
        is_active=True,
    )
    db_session.add(tomato_thread)
    await db_session.flush()

    result = await route_to_thread(
        db_session,
        farmer_id=farmer.id,
        field_id=field_a.id,  # scope has no thread
        crop_cycle_id=None,
        intent_crop_name="tomato",
    )
    assert result.id == "thr-tomato-cap"


async def test_no_intent_crop_name_skips_cross_scope(db_session):
    """When intent_crop_name is None/empty, cross-scope must not run; the
    helper must fall straight through to get_or_create_thread."""
    farmer, _, field_b, _, cycle_tomato = await _seed_farmer_with_two_fields(db_session)

    # A rice thread exists for a different field. With no intent_crop_name,
    # we must NOT cross over to it.
    db_session.add(
        ConversationThread(
            id="thr-rice",
            farmer_id=farmer.id,
            field_id="field-A",
            crop_cycle_id="cycle-rice",
            channel="telegram",
            is_active=True,
        )
    )
    await db_session.flush()

    result = await route_to_thread(
        db_session,
        farmer_id=farmer.id,
        field_id=field_b.id,
        crop_cycle_id=cycle_tomato.id,
        intent_crop_name=None,
    )
    assert result.id != "thr-rice"
    assert result.field_id == field_b.id
