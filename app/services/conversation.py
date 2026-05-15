"""
Durable conversation continuity and action impact graphs.
Low-dependency by design: deterministic retrieval, summarization, and impact scoring over
existing advisories, observations, evidence article IDs, and memory atoms.
"""
from __future__ import annotations

import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Advisory, WikiArticle
from app.models_memory import ActionImpact, ConversationThread, ConversationTurn
from app.utils.time import utc_now

FOLLOWUP_MARKERS = {
    "hi": ["अब", "फिर", "उसके बाद", "पहले", "वही", "और", "क्या करूं", "दवा", "कल"],
    "en": ["now", "then", "after that", "same", "again", "previous", "yesterday", "tomorrow"],
}


def looks_like_followup(message: str) -> bool:
    """Cheap follow-up detector for routing before any LLM call."""
    text = (message or "").lower()
    if len(text.split()) <= 5 and any(token in text for token in ["?", "क्या", "now", "again"]):
        return True
    markers = FOLLOWUP_MARKERS["hi"] + FOLLOWUP_MARKERS["en"]
    return any(marker in text for marker in markers)


async def get_or_create_thread(
    db: AsyncSession,
    farmer_id: str,
    field_id: str | None,
    crop_cycle_id: str | None,
    channel: str = "telegram",
) -> ConversationThread:
    """Return the active thread for one farmer-field-crop scope."""
    result = await db.execute(
        select(ConversationThread)
        .where(
            ConversationThread.farmer_id == farmer_id,
            ConversationThread.field_id == field_id,
            ConversationThread.crop_cycle_id == crop_cycle_id,
            ConversationThread.channel == channel,
            ConversationThread.is_active,
        )
        .order_by(desc(ConversationThread.updated_at))
        .limit(1)
    )
    thread = result.scalar_one_or_none()
    if thread:
        return thread

    thread = ConversationThread(
        id=str(uuid.uuid4()),
        farmer_id=farmer_id,
        field_id=field_id,
        crop_cycle_id=crop_cycle_id,
        channel=channel,
        title="Field conversation",
    )
    db.add(thread)
    await db.flush()
    return thread


async def latest_thread(
    db: AsyncSession,
    farmer_id: str,
    field_id: str | None = None,
    crop_cycle_id: str | None = None,
) -> ConversationThread | None:
    query = select(ConversationThread).where(ConversationThread.farmer_id == farmer_id)
    if field_id:
        query = query.where(ConversationThread.field_id == field_id)
    if crop_cycle_id:
        query = query.where(ConversationThread.crop_cycle_id == crop_cycle_id)
    result = await db.execute(query.order_by(desc(ConversationThread.updated_at)).limit(1))
    return result.scalar_one_or_none()


async def previous_evidence_article_ids(
    db: AsyncSession,
    farmer_id: str,
    field_id: str | None,
    crop_cycle_id: str | None,
    limit: int = 8,
) -> list[str]:
    """Previous article IDs for graph expansion on follow-up questions."""
    thread = await latest_thread(db, farmer_id, field_id, crop_cycle_id)
    if not thread:
        return []
    result = await db.execute(
        select(ConversationTurn)
        .where(ConversationTurn.thread_id == thread.id)
        .order_by(desc(ConversationTurn.created_at))
        .limit(limit)
    )
    ids: list[str] = []
    for turn in result.scalars().all():
        for article_id in turn.evidence_article_ids or []:
            if article_id not in ids:
                ids.append(article_id)
    return ids


async def build_conversation_context(
    db: AsyncSession,
    farmer_id: str,
    field_id: str | None,
    crop_cycle_id: str | None,
    max_turns: int = 4,
) -> str:
    """Compact context block for the agent prompt and memory verifier."""
    thread = await latest_thread(db, farmer_id, field_id, crop_cycle_id)
    if not thread:
        return ""

    result = await db.execute(
        select(ConversationTurn)
        .where(ConversationTurn.thread_id == thread.id)
        .order_by(desc(ConversationTurn.created_at))
        .limit(max_turns)
    )
    turns = list(reversed(result.scalars().all()))
    lines = ["Conversation continuity:"]
    if thread.running_summary:
        lines.append(f"  Summary: {thread.running_summary[:500]}")
    for turn in turns:
        lines.append(f"  Farmer: {turn.user_message[:180]}")
        lines.append(f"  Agent: {turn.agent_response[:220]}")
    return "\n".join(lines)


async def record_turn(
    db: AsyncSession,
    *,
    farmer_id: str,
    field_id: str | None,
    crop_cycle_id: str | None,
    observation_id: str | None,
    advisory_id: str | None,
    user_message: str,
    agent_response: str,
    detected_followup: bool,
    risk_level: str,
    confidence: str,
    retrieval_path: str,
    evidence_article_ids: list[str],
    memory_snapshot: str,
) -> ConversationTurn:
    """Persist one exchange and update the thread's rolling summary."""
    thread = await get_or_create_thread(db, farmer_id, field_id, crop_cycle_id)
    turn = ConversationTurn(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        farmer_id=farmer_id,
        field_id=field_id,
        crop_cycle_id=crop_cycle_id,
        observation_id=observation_id,
        advisory_id=advisory_id,
        user_message=user_message,
        agent_response=agent_response,
        detected_followup=detected_followup,
        risk_level=risk_level,
        confidence=confidence,
        retrieval_path=retrieval_path,
        evidence_article_ids=evidence_article_ids,
        memory_snapshot=memory_snapshot[:1500],
    )
    db.add(turn)

    thread.turn_count = (thread.turn_count or 0) + 1
    thread.last_user_message = user_message[:1500]
    thread.last_agent_message = agent_response[:2000]
    thread.last_advisory_id = advisory_id
    thread.last_observation_id = observation_id
    thread.running_summary = _update_summary(
        thread.running_summary or "",
        user_message,
        agent_response,
        risk_level,
        confidence,
    )
    thread.updated_at = utc_now()
    await db.flush()
    return turn


def _update_summary(
    old_summary: str,
    user_message: str,
    agent_response: str,
    risk_level: str,
    confidence: str,
) -> str:
    latest = (
        f"Latest: farmer asked '{user_message[:120]}'; agent gave {risk_level}/{confidence} "
        f"advice: {agent_response[:180]}"
    )
    if not old_summary:
        return latest
    return f"{old_summary[:650]} | {latest}"[-900:]


async def build_action_impact_network(
    db: AsyncSession,
    advisory_id: str,
) -> list[ActionImpact]:
    """Create deterministic impact records for all actions in an advisory."""
    advisory = await db.scalar(select(Advisory).where(Advisory.id == advisory_id))
    if not advisory:
        return []

    existing = await db.execute(select(ActionImpact).where(ActionImpact.advisory_id == advisory_id))
    existing_rows = list(existing.scalars().all())
    if existing_rows:
        return existing_rows

    article_ids = advisory.evidence_article_ids or []
    articles = []
    if article_ids:
        article_result = await db.execute(select(WikiArticle).where(WikiArticle.id.in_(article_ids)))
        articles = list(article_result.scalars().all())
    article_risks = [a.risk_level for a in articles if a.risk_level]

    # action_index must be the persisted global wiki-action index (the LLM's
    # selection), not the display position — downstream learning and outcome
    # attribution use this to link impact back to the originating article
    # action. Fall back to display position only if indices are absent
    # (degenerate input).
    actions_text = list(advisory.actions_text or [])
    selected_indices = list(advisory.selected_action_indices or [])
    if len(selected_indices) != len(actions_text):
        selected_indices = list(range(len(actions_text)))

    impacts = []
    for display_pos, (action_index, action_text) in enumerate(
        zip(selected_indices, actions_text)
    ):
        profile = _score_action(action_text, advisory.risk_level, article_risks)
        impact = ActionImpact(
            id=str(uuid.uuid4()),
            advisory_id=advisory.id,
            farmer_id=advisory.farmer_id,
            field_id=None,
            crop_cycle_id=None,
            action_index=action_index,
            action_text=action_text,
            impact_level=profile["impact_level"],
            expected_result=profile["expected_result"],
            time_horizon=profile["time_horizon"],
            dependencies=profile["dependencies"],
            risks=profile["risks"],
            metrics_delta=profile["metrics_delta"],
            affects_previous_suggestions=profile["affects_previous_suggestions"],
            evidence_article_ids=article_ids,
        )
        db.add(impact)
        impacts.append(impact)
    await db.flush()
    return impacts


def _score_action(action_text: str, risk_level: str | None, article_risks: list[str]) -> dict:
    text = action_text.lower()
    risk = risk_level or "WATCH"
    chemical = any(token in text for token in ["fungicide", "pesticide", "spray", "दवा", "छिड़काव"])
    water = any(token in text for token in ["water", "drain", "irrigation", "पानी", "निकास"])
    scout = any(token in text for token in ["monitor", "scout", "inspect", "देख", "जांच"])

    impact_level = "critical" if risk == "ESCALATE" else "high" if chemical or risk == "PREVENTIVE_ACTION" else "medium"
    if scout and risk in {"NORMAL", "WATCH"}:
        impact_level = "low"

    dependencies = []
    risks = []
    if chemical:
        dependencies.extend(["correct diagnosis", "label-approved product", "protective equipment", "no imminent rain"])
        risks.extend(["wrong chemical if diagnosis is wrong", "crop injury if dose/timing is unsafe"])
    if water:
        dependencies.extend(["field drainage/irrigation access", "local rainfall forecast"])
        risks.append("water stress if applied without soil moisture check")
    if scout:
        dependencies.append("repeat inspection in 24-72 hours")

    return {
        "impact_level": impact_level,
        "expected_result": _expected_result(chemical, water, scout, risk),
        "time_horizon": "24-72 hours" if scout else "3-7 days" if chemical or water else "same day",
        "dependencies": dependencies or ["farmer can observe the field and report outcome"],
        "risks": risks or ["low risk, but advice should be updated if symptoms worsen"],
        "metrics_delta": {
            "yield_risk": -0.25 if impact_level in {"high", "critical"} else -0.10,
            "cost": 0.20 if chemical else 0.05,
            "confidence_gain": 0.15 if scout else 0.10,
        },
        "affects_previous_suggestions": [
            "future recommendations should ask whether this action was completed",
            "outcome feedback should update field memory before repeating the same action",
            f"evidence risks considered: {', '.join(article_risks[:3])}" if article_risks else "uses current advisory evidence",
        ],
    }


def _expected_result(chemical: bool, water: bool, scout: bool, risk: str) -> str:
    if chemical:
        return "Reduce disease/pest spread only if diagnosis, product, dose, and weather timing are correct."
    if water:
        return "Lower stress or disease pressure by correcting field moisture conditions."
    if scout:
        return "Improve diagnosis confidence and avoid unnecessary spend before stronger action."
    if risk == "ESCALATE":
        return "Contain immediate damage while extension support is arranged."
    return "Incrementally reduce risk and create a better follow-up decision point."
