from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.database import async_session_factory  # noqa: E402
from app.services.agent import AgentContext, AgentOrchestrator  # noqa: E402


FARMER_ID = "83ca7f96-90a6-4144-9efd-c0f51a401863"
FIELD_ID = "51f35978-7c11-49da-b852-ea77e1f23d26"
CROP_CYCLE_ID = "04fbbcb2-cb6b-49aa-99f7-f4fac96767f2"
PHONE = "1345155802"

QUERIES = [
    "मेरे टमाटर के पौधे पर पीले धब्बे हैं",
    "PMFBY के लिए कब apply करूं",
    "और बताओ",
    "मेरे बिहार के खेत में धान में blast हो रहा है",
]

REQUIRED = {
    "intent",
    "needs_retrieval",
    "language",
    "crop_name",
    "state_or_region",
    "topic_tags",
    "is_followup",
}
ALLOWED_KEYS = REQUIRED | {
    "needs_tool_call",
    "crop_stage",
    "reason",
}
INTENTS = {
    "disease_diagnosis",
    "nutrient_advice",
    "weather_query",
    "market_query",
    "finance_query",
    "scheme_query",
    "general_chat",
    "command",
}
LANGUAGES = {"hi", "en", "mixed"}
TAGS = {
    "disease",
    "pest",
    "nutrient_deficiency",
    "water_management",
    "weather_damage",
    "market",
    "scheme",
    "general",
}


def validation_errors(parsed: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED - set(parsed))
    extra = sorted(set(parsed) - ALLOWED_KEYS)
    if missing:
        errors.append(f"missing required keys: {missing}")
    if extra:
        errors.append(f"additional keys: {extra}")
    if "intent" in parsed and parsed["intent"] not in INTENTS:
        errors.append(f"invalid intent: {parsed['intent']!r}")
    if "language" in parsed and parsed["language"] not in LANGUAGES:
        errors.append(f"invalid language: {parsed['language']!r}")
    for key in ("needs_retrieval", "needs_tool_call", "is_followup"):
        if key in parsed and not isinstance(parsed[key], bool):
            errors.append(f"{key} must be boolean, got {type(parsed[key]).__name__}")
    for key in ("crop_name", "crop_stage", "state_or_region", "reason"):
        if key in parsed and not isinstance(parsed[key], str):
            errors.append(f"{key} must be string, got {type(parsed[key]).__name__}")
    topic_tags = parsed.get("topic_tags")
    if "topic_tags" in parsed:
        if not isinstance(topic_tags, list):
            errors.append(f"topic_tags must be array, got {type(topic_tags).__name__}")
        else:
            invalid = [tag for tag in topic_tags if tag not in TAGS]
            if invalid:
                errors.append(f"invalid topic_tags: {invalid}")
            if len(topic_tags) > 3:
                errors.append(f"too many topic_tags: {len(topic_tags)}")
    return errors


async def main() -> None:
    orchestrator = AgentOrchestrator()
    async with async_session_factory() as db:
        print("DB session opened")
        print(f"farmer_id={FARMER_ID}")
        print(f"field_id={FIELD_ID}")
        print(f"crop_cycle_id={CROP_CYCLE_ID}")
        print(f"phone_format=digits length={len(PHONE)}")
        for query in QUERIES:
            print("\n---")
            print(f"query={query}")
            ctx = AgentContext(
                farmer_id=FARMER_ID,
                field_id=FIELD_ID,
                crop_cycle_id=CROP_CYCLE_ID,
                message=query,
                language="auto",
            )
            try:
                result = await orchestrator.llm.classify_intent(ctx.message, ctx.language)
                parsed = result.get("parsed", {}) or {}
                errors = validation_errors(parsed)
                print(f"raw_content={result.get('content', '')}")
                print(f"parsed={json.dumps(parsed, ensure_ascii=False, sort_keys=True)}")
                print(f"intent={parsed.get('intent')}")
                print(f"crop_name={parsed.get('crop_name')}")
                print(f"topic_tags={parsed.get('topic_tags')}")
                print(f"is_followup={parsed.get('is_followup')}")
                print(f"state_or_region={parsed.get('state_or_region')}")
                print(f"validation_error={'; '.join(errors) if errors else 'none'}")
            except Exception as exc:
                print(f"crop_name=None")
                print(f"topic_tags=None")
                print(f"is_followup=None")
                print(f"state_or_region=None")
                print(f"validation_error=exception: {exc.__class__.__name__}: {exc}")
                traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
