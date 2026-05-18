"""
Ingest market data (mandi prices, MSP, central + state schemes) from seed JSON
into SQLite so SOTA queries / dashboard widgets can hit the DB directly.

Tables created (idempotent CREATE IF NOT EXISTS, all UPSERT-friendly):
  - mandi_prices         (one row per crop+variety)
  - msp_records          (one row per state+crop+variety+season)
  - schemes              (central schemes)
  - state_schemes        (state-level schemes)

Safe to re-run; rows are replaced by primary key.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "agrimesh.db"
SEED_DIR = ROOT / "data" / "seed"


DDL = [
    """
    CREATE TABLE IF NOT EXISTS mandi_prices (
        crop_group TEXT NOT NULL,
        variety TEXT NOT NULL,
        min_rs INTEGER,
        max_rs INTEGER,
        modal_rs INTEGER,
        unit TEXT,
        last_updated TEXT,
        source TEXT,
        PRIMARY KEY (crop_group, variety)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS msp_records (
        state TEXT NOT NULL,
        crop TEXT NOT NULL,
        variety TEXT NOT NULL DEFAULT '',
        season TEXT NOT NULL DEFAULT '',
        msp_rs_per_quintal INTEGER,
        effective_date TEXT,
        source_url TEXT,
        notes TEXT,
        PRIMARY KEY (state, crop, variety, season)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schemes (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        name_hi TEXT,
        description TEXT,
        eligibility_json TEXT,
        benefit TEXT,
        apply_link TEXT,
        last_synced TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS state_schemes (
        id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        name TEXT NOT NULL,
        name_local TEXT,
        description TEXT,
        applicable_crops_json TEXT,
        benefit TEXT,
        eligibility_json TEXT,
        apply_link TEXT,
        last_synced TEXT
    )
    """,
]


def _load(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def ingest_mandi(cur: sqlite3.Cursor) -> int:
    data = _load(SEED_DIR / "mandi_prices.json")
    last_updated = str(data.get("last_updated", ""))
    source = str(data.get("source", "seed"))
    rows = 0
    for crop_group, group in data.items():
        if not isinstance(group, dict):
            continue
        for variety, entry in group.items():
            if not isinstance(entry, dict) or "modal" not in entry:
                continue
            cur.execute(
                """
                INSERT OR REPLACE INTO mandi_prices
                (crop_group, variety, min_rs, max_rs, modal_rs, unit, last_updated, source)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    crop_group,
                    variety,
                    int(entry.get("min", 0)),
                    int(entry.get("max", 0)),
                    int(entry.get("modal", 0)),
                    str(entry.get("unit", "₹/quintal")),
                    last_updated,
                    source,
                ),
            )
            rows += 1
    return rows


def ingest_msp(cur: sqlite3.Cursor) -> int:
    data = _load(SEED_DIR / "msp_by_state_crop.json")
    records = data.get("msp", []) if isinstance(data, dict) else []
    rows = 0
    for r in records:
        cur.execute(
            """
            INSERT OR REPLACE INTO msp_records
            (state, crop, variety, season, msp_rs_per_quintal, effective_date, source_url, notes)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                r.get("state"),
                r.get("crop"),
                r.get("variety") or "",
                r.get("season") or "",
                int(r.get("msp_rs_per_quintal", 0)),
                r.get("effective_date"),
                r.get("source_url"),
                r.get("notes"),
            ),
        )
        rows += 1
    return rows


def ingest_schemes(cur: sqlite3.Cursor) -> int:
    data = _load(SEED_DIR / "schemes.json")
    schemes = data.get("schemes", []) if isinstance(data, dict) else []
    now = datetime.now(timezone.utc).isoformat()
    rows = 0
    for s in schemes:
        cur.execute(
            """
            INSERT OR REPLACE INTO schemes
            (id, name, name_hi, description, eligibility_json, benefit, apply_link, last_synced)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                s.get("id"),
                s.get("name"),
                s.get("name_hi"),
                s.get("description"),
                json.dumps(s.get("eligibility", {}), ensure_ascii=False),
                s.get("benefit"),
                s.get("apply_link"),
                now,
            ),
        )
        rows += 1
    return rows


def ingest_state_schemes(cur: sqlite3.Cursor) -> int:
    path = SEED_DIR / "schemes_state_local.json"
    if not path.exists():
        return 0
    data = _load(path)
    items = data.get("state_schemes", []) if isinstance(data, dict) else []
    now = datetime.now(timezone.utc).isoformat()
    rows = 0
    for s in items:
        cur.execute(
            """
            INSERT OR REPLACE INTO state_schemes
            (id, state, name, name_local, description, applicable_crops_json,
             benefit, eligibility_json, apply_link, last_synced)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                s.get("id"),
                s.get("state"),
                s.get("name"),
                s.get("name_local"),
                s.get("description"),
                json.dumps(s.get("applicable_crops", []), ensure_ascii=False),
                s.get("benefit"),
                json.dumps(s.get("eligibility", {}), ensure_ascii=False),
                s.get("apply_link"),
                now,
            ),
        )
        rows += 1
    return rows


def main() -> int:
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        return 1
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        for stmt in DDL:
            cur.execute(stmt)
        m = ingest_mandi(cur)
        s = ingest_msp(cur)
        sc = ingest_schemes(cur)
        ss = ingest_state_schemes(cur)
        conn.commit()
    finally:
        conn.close()
    print(f"mandi_prices:  +{m} rows")
    print(f"msp_records:   +{s} rows")
    print(f"schemes:       +{sc} rows")
    print(f"state_schemes: +{ss} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
