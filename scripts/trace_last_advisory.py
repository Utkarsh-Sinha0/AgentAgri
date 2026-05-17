"""Dump the most recent Advisory + EvidenceBundle for a given telegram user.

Usage:
    python scripts/trace_last_advisory.py <telegram_user_id>

Shows what universal_kb / wiki / memory / verifier inputs the agent saw for the
last question that user asked — so we can verify the multi-source reasoning
pipeline actually engaged.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import desc, select

from app.database import async_session_factory, init_db
from app.models import Advisory, Farmer, Observation, VerifierReport
from app.models_memory import MemoryAtom


async def main(telegram_id: str) -> int:
    await init_db()
    async with async_session_factory() as db:
        farmer = await db.scalar(select(Farmer).where(Farmer.phone == telegram_id))
        if not farmer:
            print(f"No farmer registered for telegram_id={telegram_id}")
            return 1
        print(f"Farmer: {farmer.name} ({farmer.id})  village={farmer.village} district={farmer.district} preferred_lang={farmer.preferred_language}")

        advisory = await db.scalar(
            select(Advisory)
            .where(Advisory.farmer_id == farmer.id)
            .order_by(desc(Advisory.created_at))
            .limit(1)
        )
        if not advisory:
            print("No advisories yet for this farmer.")
            return 1

        obs = await db.scalar(select(Observation).where(Observation.id == advisory.observation_id))
        print("\n=== Last question ===")
        print(f"type={obs.observation_type if obs else '?'}  audio={bool(obs and obs.audio_path)}  image={bool(obs and obs.image_path)}")
        print(f"text: {(obs.text_content if obs else '')[:500]}")

        print("\n=== Advisory ===")
        print(f"risk={advisory.risk_level}  confidence={advisory.confidence}  model={advisory.model_used}  path={advisory.retrieval_path}  latency_ms={advisory.latency_ms}")
        print(f"actions: {advisory.actions_text}")
        print(f"warnings: {advisory.warnings_text}")
        print(f"contextualization: {(advisory.contextualization or '')[:400]}")

        print("\n=== Evidence sources used ===")
        wiki_ids = advisory.evidence_article_ids or []
        print(f"wiki_article_ids ({len(wiki_ids)}): {wiki_ids}")
        print(f"weather_data: {'YES' if advisory.weather_data else 'no'}")
        print(f"mandi_data: {'YES' if advisory.mandi_data else 'no'}")
        print(f"scheme_data: {'YES' if advisory.scheme_data else 'no'}")
        print(f"memory_reference (snippet): {(advisory.memory_reference or '')[:300]}")

        print("\n=== Verifier ===")
        v = await db.scalar(select(VerifierReport).where(VerifierReport.advisory_id == advisory.id))
        if v:
            print(f"passes_all={v.passes_all}  safe_fallback={getattr(v, 'safe_fallback_used', None)}")
            details = getattr(v, "details", None) or getattr(v, "report", None)
            if details:
                print(f"details: {str(details)[:500]}")
        else:
            print("(no verifier report stored)")

        print("\n=== Living memory atoms (last 5, non-redacted) ===")
        atoms = (
            await db.execute(
                select(MemoryAtom)
                .where(MemoryAtom.farmer_id == farmer.id, MemoryAtom.redacted.is_(False))
                .order_by(desc(MemoryAtom.created_at))
                .limit(5)
            )
        ).scalars().all()
        for a in atoms:
            print(f"  [{a.atom_type}] {(a.summary or '')[:120]}")
        if not atoms:
            print("  (none)")

    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/trace_last_advisory.py <telegram_user_id>")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
