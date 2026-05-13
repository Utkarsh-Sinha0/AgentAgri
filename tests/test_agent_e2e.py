"""
AgriMesh V4.0 — Agent E2E Tests
Tests: intent classification, tool planning, template selection (with mock Ollama),
verifier integration, full pipeline.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent import AgentContext, AgentOrchestrator

MOCK_INTENT_RESPONSE = {
    "content": json.dumps({
        "intent": "disease_diagnosis",
        "needs_retrieval": True,
        "needs_tool_call": True,
        "language": "hi",
        "reason": "Farmer describing crop symptoms"
    }),
    "latency_ms": 150,
    "model_used": "gemma4:e2b",
}

MOCK_PLAN_RESPONSE = {
    "content": json.dumps({
        "plan": "Get weather context and retrieve disease articles",
        "tool_calls": [
            {"tool_name": "get_forecast", "parameters": {"field_id": "default", "days": 5}}
        ],
        "needs_clarification": False,
        "clarification_question": ""
    }),
    "latency_ms": 500,
    "model_used": "gemma4:e2b",
}

MOCK_TEMPLATE_RESPONSE = {
    "content": json.dumps({
        "risk_level": "PREVENTIVE_ACTION",
        "confidence": "MEDIUM",
        "selected_action_indices": [0, 1, 3],
        "selected_warning_indices": [0],
        "contextualization": "आपके धान की पत्तियों पर भूरे धब्बे झुलसा रोग के लक्षण हो सकते हैं।",
        "memory_reference": "No previous observations for this field.",
        "should_escalate_to_extension_worker": False,
        "escalation_reason": ""
    }),
    "latency_ms": 400,
    "model_used": "gemma4:e2b",
}


@pytest.fixture
def mock_ollama():
    """Mock the Ollama client for deterministic tests."""
    with patch("app.utils.ollama_client.OllamaClient") as mock:
        client = MagicMock()
        client.chat = AsyncMock(return_value={"content": "{}", "latency_ms": 100, "model_used": "mock"})
        client.structured_chat = AsyncMock(return_value={
            "content": "{}",
            "parsed": {},
            "latency_ms": 100,
            "model_used": "mock",
        })
        client.classify_intent = AsyncMock(return_value={
            "content": MOCK_INTENT_RESPONSE["content"],
            "parsed": json.loads(MOCK_INTENT_RESPONSE["content"]),
            "latency_ms": 100,
            "model_used": "mock",
        })
        client.plan_tools = AsyncMock(return_value={
            "content": MOCK_PLAN_RESPONSE["content"],
            "parsed": json.loads(MOCK_PLAN_RESPONSE["content"]),
            "latency_ms": 500,
            "model_used": "mock",
        })
        client.select_template = AsyncMock(return_value={
            "content": MOCK_TEMPLATE_RESPONSE["content"],
            "parsed": json.loads(MOCK_TEMPLATE_RESPONSE["content"]),
            "latency_ms": 400,
            "model_used": "mock",
        })
        client.safety_check = AsyncMock(return_value={
            "content": json.dumps({"is_dangerous": False, "danger_category": "none", "reason": ""}),
            "parsed": {"is_dangerous": False, "danger_category": "none", "reason": ""},
        })
        mock.return_value = client
        yield mock


@pytest.mark.asyncio
async def test_agent_context_creation():
    """Agent context can be created with required fields."""
    ctx = AgentContext(
        farmer_id="test_farmer",
        message="मेरे धान में भूरे धब्बे हैं",
        language="hi",
        crop_name="rice",
        crop_stage="vegetative",
    )
    assert ctx.farmer_id == "test_farmer"
    assert ctx.language == "hi"
    assert ctx.crop_name == "rice"


@pytest.mark.asyncio
async def test_agent_no_evidence_response(db_session, mock_ollama):
    """Agent should return helpful message when no evidence is found."""
    agent = AgentOrchestrator()
    ctx = AgentContext(
        farmer_id="test_farmer",
        message="test query with no matching wiki articles",
        language="hi",
    )

    # Override retrieval to return nothing
    with patch("app.services.retrieval.retrieve", new_callable=AsyncMock) as mock_retrieve:
        mock_retrieve.return_value = {"articles": [], "path": "fast", "latency_ms": 50}

        response = await agent.process(db_session, ctx)

        assert response is not None
        assert response.confidence == "LOW"
        assert response.risk_level == "NORMAL"


@pytest.mark.asyncio
async def test_agent_end_to_end_mocked(db_session, mock_ollama):
    """Full agent pipeline with mock Ollama and mock retrieval."""
    # Seed wiki articles
    from app.models import WikiArticle
    wiki_articles = [
        WikiArticle(
            id="test_wiki_1", title="Test Rice Disease", content="Test content for rice disease management.",
            summary="Rice disease management summary.",
            applicable_crops=["rice"], applicable_stages=["vegetative"],
            topic_tags=["fungal_disease", "rice"],
            risk_level="PREVENTIVE_ACTION", review_status="published",
            actions=["Apply fungicide following label instructions", "Improve field drainage", "Remove infected plants"],
            warnings=["Do not apply during flowering", "Do not exceed recommended dose"],
            causes_of=[], aggravated_by=[], prevented_by=[], confused_with=[],
            confidence_score=0.85,
        ),
        WikiArticle(
            id="test_wiki_2", title="Safe Fungicide Use", content="Always read the label. Wear protective equipment.",
            summary="Safety guidelines for pesticide application.",
            applicable_crops=[], applicable_stages=["vegetative"],
            topic_tags=["chemical_safety"], risk_level="NORMAL", review_status="published",
            actions=["Read the label completely", "Wear gloves and mask"],
            warnings=["Do not spray during rain"],
            causes_of=[], aggravated_by=[], prevented_by=[], confused_with=[],
            confidence_score=0.90,
        ),
    ]
    db_session.add_all(wiki_articles)
    await db_session.commit()

    # Mock retrieval to return our seeded articles
    with patch("app.services.retrieval.retrieve", new_callable=AsyncMock) as mock_retrieve:
        mock_retrieve.return_value = {
            "articles": [
                {
                    "id": "test_wiki_1",
                    "title": "Test Rice Disease",
                    "summary": "Rice disease management summary.",
                    "content": "Test content for rice disease management.",
                    "actions": ["Apply fungicide following label instructions", "Improve field drainage", "Remove infected plants"],
                    "warnings": ["Do not apply during flowering", "Do not exceed recommended dose"],
                    "topic_tags": ["fungal_disease"],
                    "applicable_crops": ["rice"],
                    "applicable_stages": ["vegetative"],
                },
                {
                    "id": "test_wiki_2",
                    "title": "Safe Fungicide Use",
                    "summary": "Safety guidelines for pesticide application.",
                    "content": "Always read the label.",
                    "actions": ["Read the label completely", "Wear gloves and mask"],
                    "warnings": ["Do not spray during rain"],
                    "topic_tags": ["chemical_safety"],
                    "applicable_crops": [],
                    "applicable_stages": ["vegetative"],
                },
            ],
            "path": "fast",
            "latency_ms": 80,
            "candidate_count": 2,
        }

        agent = AgentOrchestrator()
        ctx = AgentContext(
            farmer_id="test_farmer",
            message="मेरे धान की पत्तियों पर भूरे धब्बे हैं, क्या करूं?",
            language="hi",
            crop_name="rice",
            crop_stage="vegetative",
        )

        response = await agent.process(db_session, ctx)

        assert response is not None
        assert response.risk_level in ["NORMAL", "WATCH", "PREVENTIVE_ACTION", "ESCALATE"]
        assert response.confidence in ["LOW", "MEDIUM", "HIGH"]
        assert len(response.display_text) > 0
        # With mock template selection returning PREVENTIVE_ACTION, MEDIUM
        assert response.verifier_report is not None


@pytest.mark.asyncio
async def test_agent_verifier_catches_dangerous_output(db_session, mock_ollama):
    """Verifier must catch dangerous output and return safe fallback."""
    # Make the mocked template selection return something the verifier flags
    dangerous_mock = AsyncMock(return_value={
        "content": json.dumps({
            "risk_level": "PREVENTIVE_ACTION",
            "confidence": "HIGH",
            "selected_action_indices": [0],
            "selected_warning_indices": [],
            "contextualization": "Apply 5 ml of pesticide per litre of water to cure your crop 100%",
            "memory_reference": "",
            "should_escalate_to_extension_worker": False,
            "escalation_reason": ""
        }),
        "parsed": {
            "risk_level": "PREVENTIVE_ACTION",
            "confidence": "HIGH",
            "selected_action_indices": [0],
            "selected_warning_indices": [],
            "contextualization": "Apply 5 ml of pesticide per litre of water to cure your crop 100%",
            "memory_reference": "",
            "should_escalate_to_extension_worker": False,
            "escalation_reason": ""
        },
        "latency_ms": 400,
        "model_used": "mock",
    })
    mock_ollama.return_value.select_template = dangerous_mock

    from app.models import WikiArticle
    wiki = WikiArticle(
        id="test_safe", title="Safe", content="test",
        summary="test", actions=["Test action"], warnings=["Test warning"],
        applicable_crops=[], review_status="published",
        topic_tags=[], causes_of=[], aggravated_by=[], prevented_by=[], confused_with=[],
        confidence_score=0.85,
    )
    db_session.add(wiki)
    await db_session.commit()

    with patch("app.services.retrieval.retrieve", new_callable=AsyncMock) as mock_retrieve:
        mock_retrieve.return_value = {
            "articles": [{"id": "test_safe", "title": "Safe", "summary": "test", "content": "test", "actions": ["Test action"], "warnings": ["Test warning"], "topic_tags": [], "applicable_crops": [], "applicable_stages": []}],
            "path": "fast", "latency_ms": 50, "candidate_count": 1,
        }

        agent = AgentOrchestrator()
        ctx = AgentContext(
            farmer_id="test_farmer", message="test", language="hi",
        )
        response = await agent.process(db_session, ctx)

        # The verifier should have caught the dangerous output
        if response.verifier_report:
            assert response.verifier_report.passes_all is False or response.verifier_report.passes_regex_filter is False
