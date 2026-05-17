"""
AgentAgri V4.0 - Universal Knowledge Base service.

Loads seeded JSON (MSP, state schemes, insurance, cold storage, crop playbooks)
and exposes metadata-filtered lookups for the crop_kb MCP server and the
agent's retrieval step. Idempotent: re-loading is safe.
"""
from __future__ import annotations

import json
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "seed"

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {}


def _load_json(name: str) -> Any:
    path = SEED_DIR / name
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_seed(force: bool = False) -> dict[str, Any]:
    """Load all seed files into the in-memory cache. Idempotent."""
    global _CACHE
    with _LOCK:
        if _CACHE and not force:
            return _CACHE
        _CACHE = {
            "msp": (_load_json("msp_by_state_crop.json") or {}).get("msp", []),
            "state_schemes": (_load_json("schemes_state_local.json") or {}).get("state_schemes", []),
            "insurance": (_load_json("insurance_policies.json") or {}).get("insurance_policies", []),
            "cold_storage": (_load_json("cold_storage_directory.json") or {}).get("cold_storage", []),
            "playbooks": {
                "rice": _load_json("crop_playbook_rice.json"),
                "wheat": _load_json("crop_playbook_wheat.json"),
            },
        }
        return _CACHE


def _ci_eq(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    return a.strip().lower() == b.strip().lower()


def get_msp(crop: str, state: str | None = None, season: str | None = None, variety: str | None = None) -> list[dict]:
    rows = load_seed()["msp"]
    out = [r for r in rows if _ci_eq(r.get("crop"), crop)]
    if state:
        out = [r for r in out if _ci_eq(r.get("state"), state)]
    if season:
        out = [r for r in out if _ci_eq(r.get("season"), season)]
    if variety:
        out = [r for r in out if _ci_eq(r.get("variety"), variety)]
    return out


def get_state_schemes(state: str | None = None, crop: str | None = None) -> list[dict]:
    rows = load_seed()["state_schemes"]
    out = rows
    if state:
        out = [r for r in out if _ci_eq(r.get("state"), state)]
    if crop:
        out = [
            r for r in out
            if "all" in (r.get("applicable_crops") or []) or crop.lower() in [c.lower() for c in (r.get("applicable_crops") or [])]
        ]
    return out


def get_insurance(crop: str | None = None, state: str | None = None, season: str | None = None) -> list[dict]:
    rows = load_seed()["insurance"]
    out = rows
    if crop:
        out = [r for r in out if _ci_eq(r.get("crop"), crop)]
    if state:
        out = [r for r in out if _ci_eq(r.get("state"), state) or _ci_eq(r.get("state"), "all")]
    if season:
        out = [r for r in out if _ci_eq(r.get("season"), season)]
    return out


def get_cold_storage(district: str | None = None, state: str | None = None, crop: str | None = None) -> list[dict]:
    rows = load_seed()["cold_storage"]
    out = rows
    if state:
        out = [r for r in out if _ci_eq(r.get("state"), state)]
    if district:
        out = [r for r in out if _ci_eq(r.get("district"), district)]
    if crop:
        out = [
            r for r in out
            if crop.lower() in [c.lower() for c in (r.get("crops_supported") or [])]
        ]
    return out


def get_playbook(crop: str) -> dict | None:
    return load_seed()["playbooks"].get(crop.strip().lower())


def get_stage_guidance(crop: str, stage: str | None = None) -> dict | list | None:
    pb = get_playbook(crop)
    if pb is None:
        return None
    if stage is None:
        return pb
    for st in pb.get("stages", []):
        if _ci_eq(st.get("name"), stage):
            return st
    return None


def get_sustainable_alternatives(crop: str, stage: str | None = None) -> list[str]:
    pb = get_playbook(crop)
    if pb is None:
        return []
    if stage:
        st = get_stage_guidance(crop, stage)
        if isinstance(st, dict):
            return st.get("sustainable_alternatives", []) or []
        return []
    out: list[str] = []
    for p in pb.get("sustainable_practices", []) or []:
        if isinstance(p, dict) and p.get("practice"):
            out.append(p["practice"])
        elif isinstance(p, str):
            out.append(p)
    return out


def retrieve(crop: str | None = None, state: str | None = None, stage: str | None = None, query: str | None = None) -> list[dict]:
    """Universal-KB retrieval. Returns metadata-tagged documents for the agent."""
    docs: list[dict] = []
    if crop:
        pb = get_playbook(crop)
        if pb:
            if stage:
                st = get_stage_guidance(crop, stage)
                if isinstance(st, dict):
                    docs.append({"kind": "universal_kb", "doc_type": "playbook_stage", "crop": crop, "stage": stage, "content": st})
            else:
                docs.append({"kind": "universal_kb", "doc_type": "playbook", "crop": crop, "content": pb})
    for row in get_msp(crop=crop or "", state=state):
        docs.append({"kind": "universal_kb", "doc_type": "msp", "crop": row.get("crop"), "state": row.get("state"), "content": row})
    for row in get_insurance(crop=crop, state=state):
        docs.append({"kind": "universal_kb", "doc_type": "insurance", "crop": row.get("crop"), "state": row.get("state"), "content": row})
    for row in get_state_schemes(state=state, crop=crop):
        docs.append({"kind": "universal_kb", "doc_type": "scheme", "state": row.get("state"), "content": row})
    return docs


@lru_cache(maxsize=1)
def is_loaded() -> bool:
    return bool(load_seed())
