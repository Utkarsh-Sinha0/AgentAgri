"""
AgentAgri V4.0 - Universal Knowledge Base service.

Loads seeded JSON (MSP, state schemes, insurance, cold storage, crop playbooks)
and exposes metadata-filtered lookups for the crop_kb MCP server and the
agent's retrieval step. Idempotent: re-loading is safe.
"""
from __future__ import annotations

import json
import re
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
            "reference_manuals": (_load_json("reference_manuals.json") or {}).get("reference_manuals", []),
            "common_issue_memory": (_load_json("universal_memory_common_issues.json") or {}).get("common_issue_memory", []),
            "encyclopedia": (_load_json("rice_encyclopedia_sections.json") or {}).get("rice_encyclopedia_sections", []),
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


def get_reference_manuals(crop: str | None = None) -> list[dict]:
    rows = load_seed()["reference_manuals"]
    if crop:
        return [r for r in rows if _ci_eq(r.get("crop"), crop)]
    return rows


def get_common_issue_memory(crop: str | None = None, query: str | None = None, limit: int = 4) -> list[dict]:
    rows = load_seed()["common_issue_memory"]
    if crop:
        rows = [r for r in rows if _ci_eq(r.get("crop"), crop)]
    if query:
        q = query.strip().lower()
        scored: list[tuple[int, dict]] = []
        for row in rows:
            aliases = [row.get("issue", ""), *(row.get("aliases") or []), *(row.get("topic_tags") or [])]
            score = sum(1 for alias in aliases if alias and alias.lower() in q)
            if score:
                scored.append((score, row))
        if scored:
            rows = [row for _, row in sorted(scored, key=lambda item: item[0], reverse=True)]
    return rows[:limit]


def get_encyclopedia_sections(crop: str | None = None, query: str | None = None, limit: int = 3) -> list[dict]:
    rows = load_seed().get("encyclopedia", []) or []
    if crop:
        rows = [r for r in rows if _ci_eq(r.get("crop"), crop)]
    if not query:
        return rows[:limit]
    q_tokens = {t for t in re.findall(r"[a-zA-Zऀ-ॿ]{3,}", query.lower()) if t}
    if not q_tokens:
        return rows[:limit]
    scored: list[tuple[int, dict]] = []
    for row in rows:
        hay = f"{row.get('heading','')} {row.get('text','')}".lower()
        score = sum(1 for tok in q_tokens if tok in hay)
        if score:
            scored.append((score, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:limit]]


def retrieve(
    crop: str | None = None,
    state: str | None = None,
    stage: str | None = None,
    query: str | None = None,
    allowed_types: set[str] | None = None,
) -> list[dict]:
    """Universal-KB retrieval. Returns metadata-tagged documents for the agent.

    allowed_types restricts which doc_types are loaded. None = load all
    (legacy behavior). Pass {"playbook","playbook_stage","official_manual",
    "common_issue_memory"} for pest/disease queries to keep MSP/scheme noise
    out of the prompt.
    """
    def _allow(t: str) -> bool:
        return allowed_types is None or t in allowed_types

    docs: list[dict] = []
    if crop and (_allow("playbook") or _allow("playbook_stage")):
        pb = get_playbook(crop)
        if pb:
            if stage and _allow("playbook_stage"):
                st = get_stage_guidance(crop, stage)
                if isinstance(st, dict):
                    docs.append({"kind": "universal_kb", "doc_type": "playbook_stage", "crop": crop, "stage": stage, "content": st})
            elif _allow("playbook"):
                docs.append({"kind": "universal_kb", "doc_type": "playbook", "crop": crop, "content": pb})
    if _allow("msp"):
        for row in get_msp(crop=crop or "", state=state):
            docs.append({"kind": "universal_kb", "doc_type": "msp", "crop": row.get("crop"), "state": row.get("state"), "content": row})
    if _allow("insurance"):
        for row in get_insurance(crop=crop, state=state):
            docs.append({"kind": "universal_kb", "doc_type": "insurance", "crop": row.get("crop"), "state": row.get("state"), "content": row})
    if _allow("scheme"):
        for row in get_state_schemes(state=state, crop=crop):
            docs.append({"kind": "universal_kb", "doc_type": "scheme", "state": row.get("state"), "content": row})
    if _allow("official_manual"):
        for row in get_reference_manuals(crop=crop):
            docs.append({"kind": "universal_kb", "doc_type": "official_manual", "crop": row.get("crop"), "content": row})
    if _allow("common_issue_memory"):
        for row in get_common_issue_memory(crop=crop, query=query):
            docs.append({"kind": "universal_kb", "doc_type": "common_issue_memory", "crop": row.get("crop"), "id": row.get("id"), "content": row})
    if _allow("encyclopedia"):
        for row in get_encyclopedia_sections(crop=crop, query=query):
            docs.append({"kind": "universal_kb", "doc_type": "encyclopedia", "crop": row.get("crop"), "id": row.get("id"), "content": row})
    return docs


@lru_cache(maxsize=1)
def is_loaded() -> bool:
    return bool(load_seed())
