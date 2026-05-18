"""
Backfill orphan-free, profile-rich farmer data for demo.

Phase A:
  1. Purge orphan farmer `a4b24a9a-4daa-4840-9249-dfdf2175669b` and its
     fields/crop_cycles/observations/advisories.
  2. Backfill missing pincode -> 811202 (Munger Bariarpur) for the cluster.
  3. Seed farmer_profiles row for every farmer missing one.

Idempotent: safe to re-run.
"""
from __future__ import annotations

import random
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "agrimesh.db"
ORPHAN_FARMER_ID = "a4b24a9a-4daa-4840-9249-dfdf2175669b"
DEFAULT_PINCODE = "811202"  # Munger -> Bariarpur (matches existing alert_cluster)
DEFAULT_DISTRICT = "Munger"
DEFAULT_TEHSIL = "Munger Sadar"
DEFAULT_VILLAGE = "Bariarpur"

random.seed(42)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def purge_orphan(cur: sqlite3.Cursor) -> dict[str, int]:
    counts = {}
    cur.execute("SELECT id FROM fields WHERE farmer_id = ?", (ORPHAN_FARMER_ID,))
    field_ids = [r[0] for r in cur.fetchall()]
    if field_ids:
        marks = ",".join("?" * len(field_ids))
        cur.execute(f"SELECT id FROM crop_cycles WHERE field_id IN ({marks})", field_ids)
        cycle_ids = [r[0] for r in cur.fetchall()]
    else:
        cycle_ids = []

    def _del_by(table: str, col: str, ids: list[str]) -> None:
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        cur.execute(f"DELETE FROM {table} WHERE {col} IN ({marks})", ids)
        counts[f"{table}.{col}"] = counts.get(f"{table}.{col}", 0) + cur.rowcount

    # Threads + turns referencing this farmer/fields/cycles
    cur.execute("SELECT id FROM conversation_threads WHERE farmer_id = ?", (ORPHAN_FARMER_ID,))
    thread_ids = [r[0] for r in cur.fetchall()]
    _del_by("conversation_turns", "thread_id", thread_ids)
    _del_by("conversation_turns", "farmer_id", [ORPHAN_FARMER_ID])
    _del_by("conversation_threads", "farmer_id", [ORPHAN_FARMER_ID])

    # NDVI + action impacts + calendar tasks tied to those fields/cycles
    _del_by("satellite_ndvi", "field_id", field_ids)
    _del_by("action_impacts", "field_id", field_ids)
    _del_by("action_impacts", "crop_cycle_id", cycle_ids)
    _del_by("crop_calendar_tasks", "cycle_id", cycle_ids)
    _del_by("memory_atoms", "field_id", field_ids)
    _del_by("memory_atoms", "crop_cycle_id", cycle_ids)

    # Find advisory + observation ids first so we can clean dependents.
    cur.execute("SELECT id FROM advisories WHERE farmer_id = ?", (ORPHAN_FARMER_ID,))
    adv_ids = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT id FROM observations WHERE farmer_id = ?", (ORPHAN_FARMER_ID,))
    obs_ids = [r[0] for r in cur.fetchall()]

    if adv_ids:
        marks = ",".join("?" * len(adv_ids))
        cur.execute(f"DELETE FROM action_impacts WHERE advisory_id IN ({marks})", adv_ids)
        counts["action_impacts_dep"] = cur.rowcount
        cur.execute(f"DELETE FROM verifier_reports WHERE advisory_id IN ({marks})", adv_ids)
        counts["verifier_reports"] = cur.rowcount
        cur.execute(f"DELETE FROM source_citations WHERE advisory_id IN ({marks})", adv_ids)
        counts["source_citations"] = cur.rowcount
        cur.execute(f"UPDATE conversation_turns SET advisory_id = NULL WHERE advisory_id IN ({marks})", adv_ids)
    if obs_ids:
        marks = ",".join("?" * len(obs_ids))
        cur.execute(f"UPDATE conversation_turns SET observation_id = NULL WHERE observation_id IN ({marks})", obs_ids)

    cur.execute("DELETE FROM advisories WHERE farmer_id = ?", (ORPHAN_FARMER_ID,))
    counts["advisories"] = cur.rowcount
    cur.execute("DELETE FROM observations WHERE farmer_id = ?", (ORPHAN_FARMER_ID,))
    counts["observations"] = cur.rowcount
    # NULL any lingering FKs from other tables (best-effort, defensive)
    def _null_by(table: str, col: str, ids: list[str]) -> None:
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        cur.execute(f"UPDATE {table} SET {col} = NULL WHERE {col} IN ({marks})", ids)

    for t in ("conversation_turns", "conversation_threads"):
        _null_by(t, "crop_cycle_id", cycle_ids)
        _null_by(t, "field_id", field_ids)

    # finance_entries also FK into crop_cycles; clear orphan-farmer finance first.
    cur.execute("DELETE FROM finance_entries WHERE farmer_id = ?", (ORPHAN_FARMER_ID,))
    counts["finance_entries"] = cur.rowcount
    _null_by("finance_entries", "crop_cycle_id", cycle_ids)

    if cycle_ids:
        marks = ",".join("?" * len(cycle_ids))
        cur.execute(f"DELETE FROM crop_cycles WHERE id IN ({marks})", cycle_ids)
        counts["crop_cycles"] = cur.rowcount
    if field_ids:
        marks = ",".join("?" * len(field_ids))
        cur.execute(f"DELETE FROM fields WHERE id IN ({marks})", field_ids)
        counts["fields"] = cur.rowcount
    _del_by("action_impacts", "farmer_id", [ORPHAN_FARMER_ID])
    _del_by("memory_atoms", "farmer_id", [ORPHAN_FARMER_ID])
    # memory_summaries is aggregate (no farmer_id) — leave alone
    # Finally delete the farmer row itself
    cur.execute("DELETE FROM farmers WHERE id = ?", (ORPHAN_FARMER_ID,))
    counts["farmers"] = cur.rowcount
    return counts


def backfill_pincodes(cur: sqlite3.Cursor) -> int:
    cur.execute(
        """
        UPDATE farmers
        SET pincode = ?,
            district = COALESCE(NULLIF(district, ''), ?),
            tehsil = COALESCE(NULLIF(tehsil, ''), ?),
            village = COALESCE(NULLIF(village, ''), ?)
        WHERE pincode IS NULL OR pincode = ''
        """,
        (DEFAULT_PINCODE, DEFAULT_DISTRICT, DEFAULT_TEHSIL, DEFAULT_VILLAGE),
    )
    return cur.rowcount


IRRIGATION = ["canal", "borewell", "rainfed", "pond"]
WATER = ["reliable", "seasonal", "unreliable"]
SOIL_TYPES = ["alluvial loam", "clay loam", "sandy loam", "black cotton"]
SOIL_TEST = ["done_recently", "done_old", "never"]
EQUIPMENT_POOLS = [
    ["manual"],
    ["power_tiller", "manual"],
    ["tractor", "power_tiller"],
    ["tractor", "thresher", "power_tiller"],
]
LABOR = ["family", "hired", "both"]
STORAGE = ["own_godown", "rental", "none", "cooperative"]
TRANSPORT = ["own_vehicle", "hired", "public"]
RISK = ["low", "medium", "high"]
CREDIT = ["kcc", "bank_loan", "none"]
INSURANCE = ["pmfby_enrolled", "none"]
MANDIS = [["Munger"], ["Munger", "Bhagalpur"], ["Munger", "Khagaria"], ["Bhagalpur"]]


def seed_profiles(cur: sqlite3.Cursor) -> int:
    cur.execute(
        """
        SELECT id FROM farmers
        WHERE id NOT IN (SELECT farmer_id FROM farmer_profiles)
        """
    )
    missing = [r[0] for r in cur.fetchall()]
    now = utcnow_iso()
    inserted = 0
    for fid in missing:
        irr = random.choice(IRRIGATION)
        eq = random.choice(EQUIPMENT_POOLS)
        pmfby = random.random() < 0.55
        cur.execute(
            """
            INSERT INTO farmer_profiles (
                id, farmer_id, farm_size_acres, irrigation_source, water_reliability,
                soil_test_status, primary_soil_type, equipment_access, labor_availability,
                storage_access, transport_access, annual_budget_rs, risk_tolerance,
                credit_access, insurance_status, organic_preference, preferred_mandis,
                nearest_mandi_km, pm_kisan_enrolled, pmfby_enrolled, kcc_holder,
                soil_health_card, profile_completeness, last_updated
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid.uuid4()),
                fid,
                round(random.uniform(0.8, 5.0), 2),
                irr,
                "reliable" if irr in {"canal", "borewell"} else random.choice(WATER),
                random.choice(SOIL_TEST),
                random.choice(SOIL_TYPES),
                __import__("json").dumps(eq),
                random.choice(LABOR),
                random.choice(STORAGE),
                random.choice(TRANSPORT),
                random.choice([15000, 25000, 40000, 60000, 90000]),
                random.choice(RISK),
                random.choice(CREDIT),
                "pmfby_enrolled" if pmfby else "none",
                random.random() < 0.15,
                __import__("json").dumps(random.choice(MANDIS)),
                round(random.uniform(3.0, 18.0), 1),
                random.random() < 0.7,
                pmfby,
                random.random() < 0.4,
                random.random() < 0.45,
                round(random.uniform(0.55, 0.95), 2),
                now,
            ),
        )
        inserted += 1
    return inserted


def main() -> int:
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        return 1
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    try:
        purged = purge_orphan(cur)
        pin_count = backfill_pincodes(cur)
        seeded = seed_profiles(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print("Purged orphan farmer:")
    for k, v in purged.items():
        print(f"  {k}: -{v}")
    print(f"Backfilled pincode on {pin_count} farmers (-> {DEFAULT_PINCODE})")
    print(f"Seeded farmer_profiles rows: +{seeded}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
