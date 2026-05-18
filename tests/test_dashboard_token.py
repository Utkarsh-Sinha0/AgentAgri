"""Sprint B1 — HMAC-signed dashboard token + /api/v1/dashboard/{token} endpoint."""
from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.utils.security import issue_dashboard_token, verify_dashboard_token


def test_token_roundtrip():
    tok = issue_dashboard_token("farmer-abc")
    assert verify_dashboard_token(tok) == "farmer-abc"


def test_token_rejects_tampered_payload():
    tok = issue_dashboard_token("farmer-abc")
    body, sig = tok.split(".", 1)
    # Flip a byte in the body — signature should no longer verify.
    bad = body[:-1] + ("A" if body[-1] != "A" else "B")
    assert verify_dashboard_token(f"{bad}.{sig}") is None


def test_token_rejects_tampered_signature():
    tok = issue_dashboard_token("farmer-abc")
    body, sig = tok.split(".", 1)
    bad_sig = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    assert verify_dashboard_token(f"{body}.{bad_sig}") is None


def test_token_rejects_expired(monkeypatch):
    tok = issue_dashboard_token("farmer-abc", ttl_seconds=1)
    monkeypatch.setattr(time, "time", lambda: time.time() + 5)
    assert verify_dashboard_token(tok) is None


def test_token_rejects_garbage():
    assert verify_dashboard_token("not-a-token") is None
    assert verify_dashboard_token("") is None
    assert verify_dashboard_token("a.b.c") is None


@pytest.mark.asyncio
async def test_endpoint_returns_payload_for_valid_token():
    from app.database import async_session_factory
    from app.main import app
    from app.models import Farmer

    async with async_session_factory() as db:
        farmer = Farmer(
            phone="555001",
            hashed_password="x",
            name="Ramu",
            district="Munger",
            pincode="811201",
            tehsil="Tarapur",
            village="Asarganj",
            preferred_language="en",
        )
        db.add(farmer)
        await db.commit()
        await db.refresh(farmer)
        fid = farmer.id

    token = issue_dashboard_token(fid)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as client:
        r = await client.get(f"/api/v1/dashboard/{token}")

    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["farmer"]["pincode"] == "811201"
    assert payload["farmer"]["name"] == "Ramu"


@pytest.mark.asyncio
async def test_endpoint_rejects_invalid_token():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://localhost") as client:
        r = await client.get("/api/v1/dashboard/not-a-real-token")
    assert r.status_code == 401
