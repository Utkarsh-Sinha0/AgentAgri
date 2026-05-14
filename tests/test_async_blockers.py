from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from app.config import settings
from app.utils.ollama_client import OllamaClient, OllamaTimeoutError


@pytest.mark.asyncio
async def test_ollama_chat_uses_async_client_and_keep_alive(monkeypatch):
    monkeypatch.setattr(settings, "ollama_keep_alive", -1)
    with patch("app.utils.ollama_client.AsyncClient.chat", new_callable=AsyncMock) as chat:
        chat.return_value = {"message": {"content": "ok"}}

        result = await OllamaClient().chat([{"role": "user", "content": "hi"}])

    assert result["content"] == "ok"
    assert chat.await_args.kwargs["keep_alive"] == -1


@pytest.mark.asyncio
async def test_ollama_timeout_raises_typed_error(monkeypatch):
    async def slow_chat(*args, **kwargs):
        await asyncio.sleep(1)
        return {"message": {"content": "late"}}

    monkeypatch.setattr(settings, "ollama_timeout_seconds", 0.001)
    monkeypatch.setattr(settings, "ollama_fallback_model", "")
    with (
        patch("app.utils.ollama_client.AsyncClient.chat", new=slow_chat),
        pytest.raises(OllamaTimeoutError),
    ):
        await OllamaClient().chat([{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_ollama_fallback_stays_active_within_cooldown(monkeypatch):
    monkeypatch.setattr(settings, "ollama_fallback_model", "fallback-model")
    monkeypatch.setattr(settings, "ollama_fallback_cooldown_seconds", 60.0)
    monkeypatch.setattr("app.utils.ollama_client.time.time", lambda: 100.0)
    client = OllamaClient(model="primary-model")

    with patch("app.utils.ollama_client.AsyncClient.chat", new_callable=AsyncMock) as chat:
        chat.side_effect = [
            RuntimeError("primary down"),
            {"message": {"content": "fallback"}},
            {"message": {"content": "still fallback"}},
        ]
        first = await client.chat([{"role": "user", "content": "hi"}])
        second = await client.chat([{"role": "user", "content": "hi"}])

    assert first["model_used"] == "fallback-model"
    assert second["model_used"] == "fallback-model"
    assert chat.await_args_list[-1].kwargs["model"] == "fallback-model"


@pytest.mark.asyncio
async def test_ollama_primary_resumes_after_cooldown(monkeypatch):
    monkeypatch.setattr(settings, "ollama_fallback_model", "fallback-model")
    monkeypatch.setattr("app.utils.ollama_client.time.time", lambda: 200.0)
    client = OllamaClient(model="primary-model")
    client._fallback_until = 199.0

    with patch("app.utils.ollama_client.AsyncClient.chat", new_callable=AsyncMock) as chat:
        chat.return_value = {"message": {"content": "primary"}}
        result = await client.chat([{"role": "user", "content": "hi"}])

    assert result["model_used"] == "primary-model"
    assert client._fallback_until is None
    assert chat.await_args.kwargs["model"] == "primary-model"


@pytest.mark.asyncio
async def test_retrieval_embedding_work_uses_threadpool(monkeypatch):
    pytest.importorskip("sentence_transformers", reason="BGE-M3 unavailable")
    from app.services import retrieval

    calls = []

    class FakeEmbedder:
        def encode(self, value, normalize_embeddings=True):
            calls.append(("encode", value, normalize_embeddings))
            return np.array([1.0, 0.0])

    async def fake_to_thread(func, *args, **kwargs):
        calls.append(("to_thread", getattr(func, "__name__", repr(func))))
        return func(*args, **kwargs)

    monkeypatch.setattr(retrieval, "get_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(retrieval.asyncio, "to_thread", fake_to_thread)

    vector = await retrieval._embed_query("rice blast")

    assert vector.tolist() == [1.0, 0.0]
    assert calls[0] == ("to_thread", "encode")
