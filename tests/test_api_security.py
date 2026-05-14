from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.degradation import DegradationLevel


def test_protected_api_requires_key_when_enabled():
    old_require = settings.require_api_key
    old_key = settings.api_key
    settings.require_api_key = True
    settings.api_key = "test-secret"
    try:
        with TestClient(app) as client:
            missing = client.get("/api/stats")
            assert missing.status_code == 401

            authorized = client.get(
                "/api/stats",
                headers={"X-AgriMesh-API-Key": "test-secret"},
            )
            assert authorized.status_code == 200
    finally:
        settings.require_api_key = old_require
        settings.api_key = old_key


def test_protected_dashboard_requires_farmer_identity_when_key_enabled():
    old_require = settings.require_api_key
    old_key = settings.api_key
    settings.require_api_key = True
    settings.api_key = "test-secret"
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/farmer-dashboard",
                headers={"X-AgriMesh-API-Key": "test-secret"},
            )
        assert response.status_code == 400
        assert "farmer_id or phone" in response.json()["detail"]
    finally:
        settings.require_api_key = old_require
        settings.api_key = old_key


def test_profile_update_rejects_invalid_numeric_values_before_db_lookup():
    with TestClient(app) as client:
        response = client.put(
            "/api/farmers/example/profile",
            json={"farm_size_acres": -1, "annual_budget_rs": -5},
        )

    assert response.status_code == 422


def test_invalid_content_length_header_is_rejected():
    with TestClient(app) as client:
        response = client.get("/health", headers={"content-length": "not-a-number"})

    assert response.status_code == 400


def test_cluster_review_rejects_invalid_action_before_mutation():
    with TestClient(app) as client:
        response = client.post(
            "/api/clusters/example/review",
            json={
                "action": "delete_everything",
                "extension_worker_id": "worker-1",
            },
        )

    assert response.status_code == 422


def test_degradation_health_endpoint_shape(monkeypatch):
    async def fake_check_health():
        return SimpleNamespace(
            level=DegradationLevel.DETERMINISTIC,
            ollama_healthy=False,
            ollama_model_available="",
            vision_healthy=False,
            wiki_available=True,
            tools_healthy=True,
            db_healthy=True,
            latency_ms=12,
            last_checked=123.0,
        )

    monkeypatch.setattr(
        "app.services.degradation.get_circuit_breaker",
        lambda: SimpleNamespace(
            check_health=fake_check_health,
            current_level=DegradationLevel.DETERMINISTIC,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/health/degradation")

    assert response.status_code == 200
    assert response.json()["level_name"] == "DETERMINISTIC"


def test_eval_public_token_can_read_eval_without_api_key(monkeypatch):
    old_require = settings.require_api_key
    old_key = settings.api_key
    old_eval_token = settings.eval_public_token
    settings.require_api_key = True
    settings.api_key = "test-secret"
    settings.eval_public_token = "judge-token"
    try:
        with TestClient(app) as client:
            missing = client.get("/api/eval/latest")
            allowed = client.get(
                "/api/eval/latest",
                headers={"X-Eval-Public-Token": "judge-token"},
            )

        assert missing.status_code == 401
        assert allowed.status_code == 200
    finally:
        settings.require_api_key = old_require
        settings.api_key = old_key
        settings.eval_public_token = old_eval_token
