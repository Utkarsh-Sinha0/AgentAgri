"""Run the farmer Q&A scorecard against the live agent + the seeded mock farmer.

Unlike app.eval_synthetic which seeds a throwaway farmer per run, this driver
uses the persistent mock farmer (phone=<telegram_user_id>) so memory_recall
and follow-up cases exercise the real Living Memory pipeline.

Each case is scored on three axes:
  - risk_check    : risk_level matches risk_min/risk_max bounds
  - evidence_check: required keywords appear; required tools were called
  - safety_check  : forbidden strings absent; "should_prompt_for_info" honored

A weighted aggregate score per case is reported. No LLM-judge for v1 — keyword
+ rule scoring is deterministic, reproducible, and surfaces structural bugs
before fancy judging. (LLM-judge can be layered in v2.)

Usage:
    python scripts/run_farmer_scorecard.py
    python scripts/run_farmer_scorecard.py --telegram-id 1345155802 --limit 5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.database import async_session_factory, init_db
from app.models import CropCycle, Farmer, Field, Observation
from app.services.agent import AgentContext, get_agent
from app.utils.time import utc_now

SCORECARD_PATH = Path("evals") / "farmer_scorecard_v1.json"
OUT_DIR = Path("evals")

RISK_ORDER = {"NORMAL": 0, "WATCH": 1, "PREVENTIVE_ACTION": 2, "ESCALATE": 3}


def _risk_in_bounds(got: str, risk_min: str | None, risk_max: str | None) -> bool:
    g = RISK_ORDER.get((got or "").upper(), -1)
    if g < 0:
        return False
    if risk_min and g < RISK_ORDER.get(risk_min.upper(), 0):
        return False
    if risk_max and g > RISK_ORDER.get(risk_max.upper(), 3):
        return False
    return True


def _contains_any(text: str, needles: list[str]) -> tuple[bool, list[str]]:
    """Return (any_match, list_of_matched_needles). Case-insensitive."""
    if not needles:
        return True, []
    t = (text or "").lower()
    hits = [n for n in needles if n and n.lower() in t]
    return bool(hits), hits


def _contains_none(text: str, forbidden: list[str]) -> tuple[bool, list[str]]:
    """Return (all_absent, list_of_violations)."""
    if not forbidden:
        return True, []
    t = (text or "").lower()
    hits = [n for n in forbidden if n and n.lower() in t]
    return not hits, hits


async def _resolve_mock_farmer(db, telegram_user_id: str) -> tuple[str, str, str]:
    """Look up the seeded mock farmer, its active field, and active crop cycle."""
    farmer = await db.scalar(select(Farmer).where(Farmer.phone == telegram_user_id))
    if not farmer:
        raise RuntimeError(
            f"No farmer with phone={telegram_user_id!r}. "
            "Run: python scripts/seed_mock_farmer.py --telegram-id <id>"
        )
    field = await db.scalar(select(Field).where(Field.farmer_id == farmer.id))
    if not field:
        raise RuntimeError(f"Farmer {farmer.id} has no field. Re-run the seed.")
    cycle = await db.scalar(
        select(CropCycle).where(CropCycle.field_id == field.id, CropCycle.is_active)
    )
    if not cycle:
        raise RuntimeError(f"Field {field.id} has no active crop cycle.")
    return farmer.id, field.id, cycle.id


def _score_case(case: dict, response) -> dict:
    """Compute per-case scores."""
    expected = case.get("expected", {})
    weights = case.get("scoring", {"risk_weight": 1, "evidence_weight": 1, "safety_weight": 1})

    display_text = response.display_text or ""

    # risk_check
    risk_min = expected.get("risk_min")
    risk_max = expected.get("risk_max")
    risk_ok = True
    if risk_min or risk_max:
        risk_ok = _risk_in_bounds(response.risk_level or "", risk_min, risk_max)

    # evidence_check: at least one of must_mention appears
    must_mention = expected.get("must_mention", [])
    evidence_ok, mention_hits = _contains_any(display_text, must_mention)

    # safety_check: none of must_NOT_contain appears
    forbidden = expected.get("must_NOT_contain", [])
    safety_ok, safety_violations = _contains_none(display_text, forbidden)

    # weighted score
    w_r = weights.get("risk_weight", 0)
    w_e = weights.get("evidence_weight", 0)
    w_s = weights.get("safety_weight", 0)
    total_weight = w_r + w_e + w_s or 1
    earned = (
        (w_r if risk_ok else 0)
        + (w_e if evidence_ok else 0)
        + (w_s if safety_ok else 0)
    )
    score = round(earned / total_weight, 3)

    return {
        "risk_ok": risk_ok,
        "got_risk": response.risk_level,
        "expected_risk_bounds": {"min": risk_min, "max": risk_max},
        "evidence_ok": evidence_ok,
        "mention_hits": mention_hits,
        "missing_mentions": [m for m in must_mention if m not in mention_hits],
        "safety_ok": safety_ok,
        "safety_violations": safety_violations,
        "score": score,
        "weights": weights,
    }


async def run(telegram_user_id: str, limit: int | None) -> dict:
    spec = json.loads(SCORECARD_PATH.read_text(encoding="utf-8"))
    cases = spec["cases"]
    if limit:
        cases = cases[:limit]

    await init_db()
    agent = get_agent()
    logger.info(
        f"Running {len(cases)} cases against agent (model={settings.ollama_model}) "
        f"as farmer {telegram_user_id}"
    )

    results: list[dict] = []
    async with async_session_factory() as db:
        farmer_id, field_id, cycle_id = await _resolve_mock_farmer(db, telegram_user_id)
        logger.info(f"Mock farmer: {farmer_id} / field {field_id} / cycle {cycle_id}")

        for i, case in enumerate(cases, 1):
            obs_id = f"obs-{uuid.uuid4().hex[:8]}"
            db.add(Observation(
                id=obs_id,
                farmer_id=farmer_id,
                crop_cycle_id=cycle_id,
                field_id=field_id,
                observation_type="text",
                text_content=case.get("message", ""),
                reported_stage="vegetative",
            ))
            await db.commit()

            ctx = AgentContext(
                farmer_id=farmer_id,
                field_id=field_id,
                crop_cycle_id=cycle_id,
                observation_id=obs_id,
                message=case.get("message", ""),
                language=case.get("language", "hi"),
                crop_name="rice",
                crop_stage="vegetative",
            )

            t0 = time.perf_counter()
            try:
                resp = await agent.process(db, ctx)
                latency_ms = int((time.perf_counter() - t0) * 1000)
                scoring = _score_case(case, resp)
                results.append({
                    "id": case["id"],
                    "category": case.get("category"),
                    "language": case.get("language"),
                    "message": case.get("message"),
                    "latency_ms": latency_ms,
                    "display_text": (resp.display_text or "")[:500],
                    "retrieval_path": resp.retrieval_path,
                    "verifier_passed": bool(
                        resp.verifier_report and resp.verifier_report.passes_all
                    ),
                    "evidence_cards_count": len(resp.evidence_cards or []),
                    **scoring,
                })
                logger.info(
                    f"[{i}/{len(cases)}] {case['id']} {case.get('category')}: "
                    f"score={scoring['score']:.2f} risk={scoring['got_risk']} "
                    f"ev={'OK' if scoring['evidence_ok'] else 'X'} "
                    f"sf={'OK' if scoring['safety_ok'] else 'X'} {latency_ms}ms"
                )
            except Exception as exc:
                latency_ms = int((time.perf_counter() - t0) * 1000)
                results.append({
                    "id": case["id"],
                    "category": case.get("category"),
                    "error": str(exc)[:400],
                    "latency_ms": latency_ms,
                    "score": 0.0,
                })
                logger.exception(f"[{i}/{len(cases)}] {case['id']}: ERROR")

    summary = _aggregate(results)
    _write_outputs(results, summary)
    return summary


def _aggregate(results: list[dict]) -> dict:
    ok = [r for r in results if "error" not in r]
    errs = [r for r in results if "error" in r]
    total = len(results) or 1
    overall_score = round(sum(r.get("score", 0) for r in results) / total, 3)

    from collections import defaultdict
    by_cat: dict[str, list[float]] = defaultdict(list)
    for r in results:
        by_cat[r.get("category", "unknown")].append(r.get("score", 0))

    cat_table = {c: round(sum(v) / len(v), 3) for c, v in by_cat.items()}

    return {
        "timestamp": utc_now().isoformat(),
        "model": settings.ollama_model,
        "total": len(results),
        "completed": len(ok),
        "errors": len(errs),
        "overall_score": overall_score,
        "by_category": cat_table,
        "risk_ok_rate": round(sum(1 for r in ok if r.get("risk_ok")) / max(len(ok), 1), 3),
        "evidence_ok_rate": round(sum(1 for r in ok if r.get("evidence_ok")) / max(len(ok), 1), 3),
        "safety_ok_rate": round(sum(1 for r in ok if r.get("safety_ok")) / max(len(ok), 1), 3),
        "latency_p50_ms": _percentile([r.get("latency_ms", 0) for r in ok], 50),
        "latency_p95_ms": _percentile([r.get("latency_ms", 0) for r in ok], 95),
    }


def _percentile(values: list[int], p: int) -> int:
    if not values:
        return 0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[idx]


def _write_outputs(results: list[dict], summary: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = utc_now().strftime("%Y%m%d_%H%M%S")
    (OUT_DIR / f"farmer_scorecard_results_{ts}.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUT_DIR / f"farmer_scorecard_summary_{ts}.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUT_DIR / "farmer_scorecard_latest.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(f"Wrote evals/farmer_scorecard_*_{ts}.json (latest: farmer_scorecard_latest.json)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--telegram-id", default="1345155802")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    summary = asyncio.run(run(args.telegram_id, args.limit))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
