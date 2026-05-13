"""
AgriMesh V4.0 — E4B Grammar-Constrained Decoding Tests
Validates: schema loading, grammar JSON validity, Ollama format param compatibility.
"""
import json
from pathlib import Path

SCHEMAS_DIR = Path(__file__).parent.parent / "app" / "schemas"


class TestGrammarSchemas:

    def test_all_schemas_exist(self):
        """Every required schema file must exist."""
        required = [
            "intent_classification.schema.json",
            "tool_call.schema.json",
            "template_selection.schema.json",
            "safety_check.schema.json",
            "cluster_summary.schema.json",
        ]
        for name in required:
            path = SCHEMAS_DIR / name
            assert path.exists(), f"Missing schema: {name}"

    def test_all_schemas_are_valid_json(self):
        """Every schema must parse as valid JSON."""
        for path in SCHEMAS_DIR.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            assert isinstance(data, dict), f"{path.name}: not a dict"
            assert "$schema" in data or "type" in data, f"{path.name}: missing $schema or type"

    def test_intent_classification_constrains_enum(self):
        """Intent schema must constrain intent to a fixed enum."""
        schema = json.loads((SCHEMAS_DIR / "intent_classification.schema.json").read_text())
        intent_enum = schema["properties"]["intent"]["enum"]
        assert "disease_diagnosis" in intent_enum
        assert "general_chat" in intent_enum

    def test_template_selection_constrains_risk_level(self):
        """Template selection must constrain risk_level enum."""
        schema = json.loads((SCHEMAS_DIR / "template_selection.schema.json").read_text())
        risk_enum = schema["properties"]["risk_level"]["enum"]
        assert "NORMAL" in risk_enum
        assert "PREVENTIVE_ACTION" in risk_enum
        assert "ESCALATE" in risk_enum

    def test_template_selection_constrains_confidence(self):
        """Template selection must constrain confidence enum."""
        schema = json.loads((SCHEMAS_DIR / "template_selection.schema.json").read_text())
        conf_enum = schema["properties"]["confidence"]["enum"]
        assert "LOW" in conf_enum
        assert "HIGH" in conf_enum

    def test_action_indices_range(self):
        """Action indices must have minItems 1, maxItems 5."""
        schema = json.loads((SCHEMAS_DIR / "template_selection.schema.json").read_text())
        action_schema = schema["properties"]["selected_action_indices"]
        assert action_schema["minItems"] == 1
        assert action_schema["maxItems"] == 5

    def test_warning_indices_optional(self):
        """Warning indices may be empty (maxItems 3, no minItems)."""
        schema = json.loads((SCHEMAS_DIR / "template_selection.schema.json").read_text())
        warning_schema = schema["properties"]["selected_warning_indices"]
        assert warning_schema["maxItems"] == 3

    def test_additional_properties_false(self):
        """All schemas should reject additionalProperties."""
        for path in SCHEMAS_DIR.glob("*.json"):
            schema = json.loads(path.read_text(encoding="utf-8"))
            if schema.get("type") == "object":
                assert not schema.get("additionalProperties"), (
                    f"{path.name}: additionalProperties should be false"
                )

    def test_tool_call_schema_constrains_tool_name(self):
        """Tool call schema must constrain tool_name to known MCP tools."""
        schema = json.loads((SCHEMAS_DIR / "tool_call.schema.json").read_text())
        tool_names = schema["properties"]["tool_calls"]["items"]["properties"]["tool_name"]["enum"]
        assert "get_forecast" in tool_names
        assert "get_mandi_prices" in tool_names
        assert "match_schemes" in tool_names
        assert "no_tool" in tool_names

    def test_safety_schema_danger_categories(self):
        """Safety schema must have explicit danger categories."""
        schema = json.loads((SCHEMAS_DIR / "safety_check.schema.json").read_text())
        categories = schema["properties"]["danger_category"]["enum"]
        assert "chemical_dosage" in categories
        assert "medical_guarantee" in categories
        assert "scheme_promise" in categories


class TestSafetyRegex:

    def test_english_dosage_detected(self):
        """Regex must catch English chemical dosage patterns."""
        import asyncio

        from app.utils.safety import check_safety_regex
        result = asyncio.run(check_safety_regex("Apply 5 ml per litre of water to your crops"))
        assert not result["passes"]
        assert any("chemical_dosage" in m["category"] for m in result["matches"])

    def test_hindi_dosage_detected(self):
        """Regex must catch Hindi chemical dosage patterns."""
        import asyncio

        from app.utils.safety import check_safety_regex
        result = asyncio.run(check_safety_regex("कीटनाशक 10 मिली प्रति लीटर डालें"))
        assert not result["passes"]

    def test_guarantee_detected(self):
        """Regex must catch medical guarantee patterns."""
        import asyncio

        from app.utils.safety import check_safety_regex
        result = asyncio.run(check_safety_regex("This will 100% cure your crop problem"))
        assert not result["passes"]

    def test_safe_text_passes(self):
        """Safe text must pass the regex filter."""
        import asyncio

        from app.utils.safety import check_safety_regex
        result = asyncio.run(check_safety_regex(
            "Monitor your crop daily. Contact your KVK for advice. Follow label instructions."
        ))
        assert result["passes"]


class TestVerifier:

    def test_verifier_empty_indices(self):
        """Verifier must flag empty action indices."""
        import asyncio

        from app.services.verifier import EvidenceBundle, Recommendation, VerifierService

        verifier = VerifierService()
        rec = Recommendation(
            risk_level="ESCALATE",
            confidence="HIGH",
            selected_action_indices=[],
            selected_warning_indices=[],
            actions_text=[],
            warnings_text=[],
            contextualization="test",
        )
        evidence = EvidenceBundle(wiki_articles=[{"actions": ["Test action"], "warnings": ["Test warning"]}])
        report, fallback = asyncio.run(verifier.verify(rec, evidence))

        assert report.indices_in_range is True  # empty is technically in range
        assert report.actions_exist_in_wiki is True  # empty is vacuously true
        # But actions_match_risk should fail — ESCALATE with no actions
        assert report.actions_match_risk_type is False

    def test_verifier_out_of_range_indices(self):
        """Verifier must catch out-of-range indices."""
        import asyncio

        from app.services.verifier import EvidenceBundle, Recommendation, VerifierService

        verifier = VerifierService()
        rec = Recommendation(
            risk_level="PREVENTIVE_ACTION",
            confidence="MEDIUM",
            selected_action_indices=[0, 5, 99],  # 99 is out of range (only 2 actions)
            selected_warning_indices=[0],
            actions_text=["Act1", "Act2"],
            warnings_text=["Warn1"],
            contextualization="test",
        )
        evidence = EvidenceBundle(wiki_articles=[{"actions": ["A1"], "warnings": ["W1"]}, {"actions": ["A2"], "warnings": []}])
        report, fallback = asyncio.run(verifier.verify(rec, evidence))

        assert report.indices_in_range is False  # 99 > 1 (total actions = 2)
        assert report.actions_exist_in_wiki is False  # index 99 doesn't exist

    def test_verifier_high_confidence_low_evidence(self):
        """HIGH confidence with only 1 article should fail calibration."""
        import asyncio

        from app.services.verifier import EvidenceBundle, Recommendation, VerifierService

        verifier = VerifierService()
        rec = Recommendation(
            risk_level="WATCH",
            confidence="HIGH",
            selected_action_indices=[0],
            selected_warning_indices=[],
            actions_text=["Act1"],
            warnings_text=[],
            contextualization="test",
        )
        evidence = EvidenceBundle(wiki_articles=[{"actions": ["A1", "A2"], "warnings": ["W1"]}])  # Only 1 article
        report, fallback = asyncio.run(verifier.verify(rec, evidence))

        assert report.confidence_calibrated_to_evidence is False
        assert report.passes_all is False


class TestAdaptiveRouter:

    def test_fast_path_simple_query(self):
        """Simple single-topic query should go to fast path."""
        from app.services.retrieval import classify_retrieval_path
        assert classify_retrieval_path("what is rice blast?", False) == "fast"

    def test_graph_path_multi_evidence(self):
        """Multi-evidence query should go to graph path."""
        from app.services.retrieval import classify_retrieval_path
        assert classify_retrieval_path(
            "rain and fungus on my rice crop how to manage", False
        ) == "graph"

    def test_graph_path_followup(self):
        """Follow-up queries should go to graph path."""
        from app.services.retrieval import classify_retrieval_path
        assert classify_retrieval_path("what to do next?", True) == "graph"


class TestOllamaClient:

    def test_thinking_mode_is_applied_to_user_message_without_mutating_input(self):
        """Thinking mode must preserve system prompt cache locality and affect the request."""
        import asyncio
        from unittest.mock import MagicMock, patch

        from app.utils.ollama_client import OllamaClient

        messages = [
            {"role": "system", "content": "static system"},
            {"role": "user", "content": "plan tools"},
        ]

        with patch("app.utils.ollama_client.ollama.Client") as client_cls:
            client = MagicMock()
            client.chat.return_value = {"message": {"content": "{}"}}
            client_cls.return_value = client

            result = asyncio.run(OllamaClient().chat(messages, thinking=True))

        sent_messages = client.chat.call_args.kwargs["messages"]
        assert sent_messages[0]["content"] == "static system"
        assert sent_messages[1]["content"].startswith("<|think|>\n")
        assert messages[1]["content"] == "plan tools"
        assert result["content"] == "{}"
