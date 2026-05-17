"""
Synthetic-set evaluation harness.

Consumes evals/golden_synthetic_v1.json (the DeepSeek-generated 200-case set)
and scores the live agent against the structured `expected_advisory` and
`must_NOT_contain` fields.

Metrics (per-category and overall):
  - risk_level_exact   : response.risk_level == expected.risk_level
  - confidence_match   : response.confidence == expected.confidence
  - safety_pass        : none of must_NOT_contain substrings appear in display_text
  - verifier_pass      : response.verifier_report.passes_all
  - latency_ms         : per-case wall time
  - error_rate         : cases that raised

Outputs:
  evals/synthetic_results_<ts>.json   (per-case)
  evals/synthetic_summary_<ts>.json   (aggregate)
  evals/synthetic_latest.json         (aggregate, stable name for dashboards)
  evals/synthetic_report_<ts>.md      (human-readable)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from collections import defaultdict
from pathlib import Path

import numpy as np
from loguru import logger

from app.config import settings
from app.database import async_session_factory, init_db
from app.models import CropCycle, Farmer, Field, Observation
from app.services.agent import AgentContext, get_agent
from app.utils.security import hash_password
from app.utils.time import utc_now

EVAL_PATH = Path("evals") / "golden_synthetic_v1.json"
OUT_DIR = Path("evals")


def load_cases(limit: int | None, categories: list[str] | None, source: Path | None = None) -> list[dict]:
    data = json.loads((source or EVAL_PATH).read_text(encoding="utf-8"))
    if categories:
        data = [c for c in data if c.get("category") in set(categories)]
    if limit:
        data = data[:limit]
    return data


async def _seed_farmer(db) -> tuple[str, str, str]:
    """Insert a throwaway farmer/field/cycle so the agent has FK targets."""
    farmer_id = f"eval-{uuid.uuid4().hex[:8]}"
    field_id = f"field-{uuid.uuid4().hex[:8]}"
    cycle_id = f"cycle-{uuid.uuid4().hex[:8]}"
    db.add_all([
        Farmer(
            id=farmer_id,
            phone=f"eval-{farmer_id}",
            hashed_password=hash_password("eval"),
            name="Eval Farmer",
            district="Munger",
            village="Bariarpur",
        ),
        Field(id=field_id, farmer_id=farmer_id, name="Eval Field", area_acres=1.0),
        CropCycle(
            id=cycle_id,
            field_id=field_id,
            crop_name="rice",
            sowing_date=utc_now(),
            current_stage="vegetative",
            is_active=True,
        ),
    ])
    await db.commit()
    return farmer_id, field_id, cycle_id


def _safety_pass(text: str, forbidden: list[str]) -> bool:
    text_l = (text or "").lower()
    return all(not (needle and needle.lower() in text_l) for needle in forbidden or [])


async def run(limit: int | None, categories: list[str] | None, source: Path | None = None) -> dict:
    cases = load_cases(limit, categories, source)
    logger.info(f"Running synthetic eval on {len(cases)} cases (model={settings.ollama_model})")

    await init_db()
    agent = get_agent()
    results: list[dict] = []

    async with async_session_factory() as db:
        farmer_id, field_id, cycle_id = await _seed_farmer(db)

        for i, case in enumerate(cases, 1):
            persona = case.get("farmer_persona", {})
            inp = case.get("input", {})
            expected = case.get("expected_advisory", {})

            obs_id = f"obs-{uuid.uuid4().hex[:8]}"
            db.add(Observation(
                id=obs_id,
                farmer_id=farmer_id,
                crop_cycle_id=cycle_id,
                field_id=field_id,
                observation_type="text",
                text_content=inp.get("user_message", ""),
                reported_stage=persona.get("stage"),
            ))
            await db.commit()

            ctx = AgentContext(
                farmer_id=farmer_id,
                field_id=field_id,
                crop_cycle_id=cycle_id,
                observation_id=obs_id,
                message=inp.get("user_message", ""),
                language=case.get("language", "hi"),
                crop_name=persona.get("crop"),
                crop_stage=persona.get("stage"),
            )

            t0 = time.perf_counter()
            try:
                resp = await agent.process(db, ctx)
                latency_ms = int((time.perf_counter() - t0) * 1000)

                got_risk = resp.risk_level or ""
                got_conf = resp.confidence or ""
                exp_risk = expected.get("risk_level", "")
                exp_conf = expected.get("confidence", "")

                risk_match = got_risk == exp_risk
                conf_match = got_conf == exp_conf
                safety_ok = _safety_pass(resp.display_text, case.get("must_NOT_contain", []))
                verifier_ok = bool(resp.verifier_report and resp.verifier_report.passes_all)

                results.append({
                    "id": case["id"],
                    "category": case.get("category"),
                    "language": case.get("language"),
                    "got_risk": got_risk,
                    "expected_risk": exp_risk,
                    "risk_match": risk_match,
                    "got_conf": got_conf,
                    "expected_conf": exp_conf,
                    "conf_match": conf_match,
                    "safety_pass": safety_ok,
                    "verifier_pass": verifier_ok,
                    "retrieval_path": resp.retrieval_path,
                    "latency_ms": latency_ms,
                    "display_text_excerpt": (resp.display_text or "")[:240],
                })
                logger.info(
                    f"[{i}/{len(cases)}] {case['id']} {case.get('category')}: "
                    f"risk {got_risk}/{exp_risk} {'OK' if risk_match else 'X'} "
                    f"safety={'OK' if safety_ok else 'X'} {latency_ms}ms"
                )
            except Exception as exc:
                latency_ms = int((time.perf_counter() - t0) * 1000)
                results.append({
                    "id": case["id"],
                    "category": case.get("category"),
                    "error": str(exc)[:300],
                    "latency_ms": latency_ms,
                })
                logger.error(f"[{i}/{len(cases)}] {case['id']}: ERROR {exc}")

    summary = _aggregate(results)
    _write_outputs(results, summary)
    return summary


def _aggregate(results: list[dict]) -> dict:
    ok = [r for r in results if "error" not in r]
    errs = [r for r in results if "error" in r]

    def rate(field: str) -> float:
        return round(sum(1 for r in ok if r.get(field)) / max(len(ok), 1), 3)

    by_cat: dict[str, dict] = defaultdict(lambda: {"n": 0, "risk_match": 0, "conf_match": 0, "safety_pass": 0, "verifier_pass": 0})
    for r in ok:
        c = r.get("category", "unknown")
        by_cat[c]["n"] += 1
        for k in ("risk_match", "conf_match", "safety_pass", "verifier_pass"):
            if r.get(k):
                by_cat[c][k] += 1

    cat_table = {}
    for c, agg in by_cat.items():
        n = agg["n"] or 1
        cat_table[c] = {
            "n": agg["n"],
            "risk_match": round(agg["risk_match"] / n, 3),
            "conf_match": round(agg["conf_match"] / n, 3),
            "safety_pass": round(agg["safety_pass"] / n, 3),
            "verifier_pass": round(agg["verifier_pass"] / n, 3),
        }

    lat = np.array([r["latency_ms"] for r in ok]) if ok else np.array([0])
    return {
        "timestamp": utc_now().isoformat(),
        "model": settings.ollama_model,
        "total": len(results),
        "completed": len(ok),
        "errors": len(errs),
        "risk_match_rate": rate("risk_match"),
        "conf_match_rate": rate("conf_match"),
        "safety_pass_rate": rate("safety_pass"),
        "verifier_pass_rate": rate("verifier_pass"),
        "latency_p50_ms": int(np.percentile(lat, 50)),
        "latency_p95_ms": int(np.percentile(lat, 95)),
        "by_category": cat_table,
    }


def _write_outputs(results: list[dict], summary: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = utc_now().strftime("%Y%m%d_%H%M%S")
    (OUT_DIR / f"synthetic_results_{ts}.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUT_DIR / f"synthetic_summary_{ts}.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUT_DIR / "synthetic_latest.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (OUT_DIR / f"synthetic_report_{ts}.md").write_text(_md(summary), encoding="utf-8")
    logger.info(f"Wrote evals/synthetic_*_{ts}.{{json,md}} (latest: synthetic_latest.json)")


def _md(s: dict) -> str:
    lines = [
        "# AgentAgri — Synthetic Eval Report",
        f"- Timestamp: {s['timestamp']}",
        f"- Model: `{s['model']}`",
        f"- Total: {s['total']} | Completed: {s['completed']} | Errors: {s['errors']}",
        "",
        "## Aggregate",
        "| Metric | Rate |",
        "|---|---|",
        f"| Risk-level exact match | {s['risk_match_rate']:.1%} |",
        f"| Confidence exact match | {s['conf_match_rate']:.1%} |",
        f"| Safety pass (no forbidden strings) | {s['safety_pass_rate']:.1%} |",
        f"| Verifier pass | {s['verifier_pass_rate']:.1%} |",
        f"| Latency p50 | {s['latency_p50_ms']} ms |",
        f"| Latency p95 | {s['latency_p95_ms']} ms |",
        "",
        "## By Category",
        "| Category | n | Risk | Conf | Safety | Verifier |",
        "|---|---|---|---|---|---|",
    ]
    for cat, v in sorted(s["by_category"].items()):
        lines.append(
            f"| {cat} | {v['n']} | {v['risk_match']:.1%} | {v['conf_match']:.1%} | "
            f"{v['safety_pass']:.1%} | {v['verifier_pass']:.1%} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None, help="run only the first N cases")
    p.add_argument("--categories", type=str, default=None,
                   help="comma-separated category filter (diagnosis,escalate,...)")
    p.add_argument("--source", type=str, default=None,
                   help="override path to golden eval JSON (defaults to evals/golden_synthetic_v1.json)")
    args = p.parse_args()
    cats = [c.strip() for c in args.categories.split(",")] if args.categories else None
    src = Path(args.source) if args.source else None
    summary = asyncio.run(run(args.limit, cats, src))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
