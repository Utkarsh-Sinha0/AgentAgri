from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from app.api.v1 import auth
from app.config import settings
from app.main import app

SECRET = "x" * 32
FARMER_ID = "farmer_demo_munger_001"
WORKER_ID = "worker_demo_extension_001"


def _decode_segment(segment: str) -> dict:
    padded = segment + ("=" * (-len(segment) % 4))
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))


def _client() -> TestClient:
    return TestClient(app)


def _configure_demo(monkeypatch, enabled: bool = True) -> None:
    auth._ip_buckets.clear()
    auth._used_jti.clear()
    monkeypatch.setattr(settings, "bot_demo_secret_current", SECRET)
    monkeypatch.setattr(settings, "bot_demo_secret_previous", "")
    monkeypatch.setattr(settings, "enable_demo_sessions", enabled)
    monkeypatch.setattr(settings, "demo_allowed_farmers", [FARMER_ID])
    monkeypatch.setattr(settings, "demo_allowed_workers", [WORKER_ID])


def test_demo_session_rejects_missing_and_wrong_header(monkeypatch):
    _configure_demo(monkeypatch)

    missing = _client().post(
        f"/api/auth/demo-session?role=farmer&identity={FARMER_ID}",
    )
    wrong = _client().post(
        f"/api/auth/demo-session?role=farmer&identity={FARMER_ID}",
        headers={"X-Bot-Demo-Secret": "wrong"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_demo_session_rejects_unknown_identity(monkeypatch):
    _configure_demo(monkeypatch)

    response = _client().post(
        "/api/auth/demo-session?role=farmer&identity=unknown",
        headers={"X-Bot-Demo-Secret": SECRET},
    )

    assert response.status_code == 403


def test_demo_session_rejects_when_disabled(monkeypatch):
    _configure_demo(monkeypatch, enabled=False)

    response = _client().post(
        f"/api/auth/demo-session?role=farmer&identity={FARMER_ID}",
        headers={"X-Bot-Demo-Secret": SECRET},
    )

    assert response.status_code == 403


def test_demo_session_happy_path_returns_three_segment_token(monkeypatch):
    _configure_demo(monkeypatch)

    response = _client().post(
        f"/api/auth/demo-session?role=farmer&identity={FARMER_ID}",
        headers={"X-Bot-Demo-Secret": SECRET},
    )

    assert response.status_code == 200
    data = response.json()
    token = data["bridge_token"]
    parts = token.split(".")
    assert data["expires_in"] == 300
    assert "__Host-agri_session=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert len(parts) == 3
    assert _decode_segment(parts[0]) == {"alg": "HS256", "kid": "current"}
    payload = _decode_segment(parts[1])
    assert payload["aud"] == "agri-pwa"
    assert payload["sub"] == FARMER_ID
    assert payload["role"] == "farmer"
    assert payload["single_use"] is True


def test_demo_session_two_calls_produce_different_jtis(monkeypatch):
    _configure_demo(monkeypatch)
    client = _client()

    first = client.post(
        f"/api/auth/demo-session?role=extension_worker&identity={WORKER_ID}",
        headers={"X-Bot-Demo-Secret": SECRET},
    )
    second = client.post(
        f"/api/auth/demo-session?role=extension_worker&identity={WORKER_ID}",
        headers={"X-Bot-Demo-Secret": SECRET},
    )

    first_payload = _decode_segment(first.json()["bridge_token"].split(".")[1])
    second_payload = _decode_segment(second.json()["bridge_token"].split(".")[1])
    assert first.status_code == 200
    assert second.status_code == 200
    assert first_payload["jti"] != second_payload["jti"]


def test_demo_session_seventh_call_is_rate_limited(monkeypatch):
    _configure_demo(monkeypatch)
    client = _client()

    statuses = [
        client.post(
            f"/api/auth/demo-session?role=farmer&identity={FARMER_ID}",
            headers={"X-Bot-Demo-Secret": SECRET},
        ).status_code
        for _ in range(7)
    ]

    assert statuses[:6] == [200] * 6
    assert statuses[6] == 429
