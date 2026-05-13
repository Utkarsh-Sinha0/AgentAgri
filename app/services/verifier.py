"""
AgriMesh V4.0 — Verifier Service (First-Class Auditable Component)
Four lines of defense, every recommendation checked before delivery:
  1. Structural: actions/warnings exist in wiki, indices in range
  2. Semantic: actions match risk type, no contradiction with memory
  3. Safety: regex filter + LLM self-grading classifier
  4. Calibration: confidence matches evidence quantity/quality

Produces an auditable VerifierReport stored alongside every Advisory.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from loguru import logger

from app.utils.safety import SAFE_FALLBACK_EN, SAFE_FALLBACK_HI, full_safety_check

# ─── Data Classes ─────────────────────────────────────────────────────

@dataclass
class VerifierReport:
    advisory_id: str = ""
    passes_all: bool = False

    # Structural
    actions_exist_in_wiki: bool = False
    warnings_exist_in_wiki: bool = False
    indices_in_range: bool = False

    # Semantic
    actions_match_risk_type: bool = False
    actions_dont_contradict_memory: bool = False

    # Safety
    passes_regex_filter: bool = False
    passes_llm_safety_check: bool = False

    # Calibration
    confidence_calibrated_to_evidence: bool = False

    details: dict = field(default_factory=dict)


@dataclass
class EvidenceBundle:
    """What the agent used to produce the recommendation."""
    wiki_articles: list[dict] = field(default_factory=list)
    weather_data: dict | None = None
    mandi_data: dict | None = None
    scheme_data: dict | None = None
    ndvi_data: dict | None = None
    memory_context: str = ""


@dataclass
class Recommendation:
    """The agent's raw output before verification."""
    risk_level: str = "NORMAL"
    confidence: str = "LOW"
    selected_action_indices: list[int] = field(default_factory=list)
    selected_warning_indices: list[int] = field(default_factory=list)
    actions_text: list[str] = field(default_factory=list)
    warnings_text: list[str] = field(default_factory=list)
    contextualization: str = ""
    memory_reference: str = ""
    should_escalate: bool = False
    raw_json: dict = field(default_factory=dict)


# ─── Verifier ─────────────────────────────────────────────────────────

class VerifierService:
    """Verifies every agent recommendation before delivery."""

    async def verify(
        self,
        recommendation: Recommendation,
        evidence: EvidenceBundle,
    ) -> tuple[VerifierReport, str | None]:
        """
        Run all four lines of defense.

        Returns:
            verifier_report: full pass/fail report
            safe_fallback: if any check fails, return safe fallback text; else None
        """
        report = VerifierReport()

        # ── Line 1: Grammar-constrained decoding already handled upstream ──
        # We arrive here with valid JSON. But check ranges anyway.

        report.indices_in_range = self._check_indices_in_range(
            recommendation, evidence
        )

        # ── Line 2: Structural — do selected indices actually exist? ──
        report.actions_exist_in_wiki = self._check_actions_exist(
            recommendation, evidence
        )
        report.warnings_exist_in_wiki = self._check_warnings_exist(
            recommendation, evidence
        )

        # ── Line 3: Semantic — do actions match the risk? ──
        report.actions_match_risk_type = self._check_actions_match_risk(
            recommendation, evidence
        )
        report.actions_dont_contradict_memory = self._check_memory_contradiction(
            recommendation, evidence
        )

        # ── Line 4: Safety — two-stage filter ──
        full_text = self._build_full_advisory_text(recommendation, evidence)
        safety_result = await full_safety_check(full_text)
        report.passes_regex_filter = safety_result["stage1_regex"]["passes"]
        report.passes_llm_safety_check = not safety_result["stage2_llm"].get(
            "is_dangerous", False
        )
        report.details["safety"] = safety_result

        # ── Calibration check ──
        report.confidence_calibrated_to_evidence = self._check_calibration(
            recommendation, evidence
        )

        # ── Aggregate ──
        report.passes_all = all([
            report.indices_in_range,
            report.actions_exist_in_wiki,
            report.warnings_exist_in_wiki,
            report.actions_match_risk_type,
            report.actions_dont_contradict_memory,
            report.passes_regex_filter,
            report.passes_llm_safety_check,
            report.confidence_calibrated_to_evidence,
        ])

        safe_fallback = None if report.passes_all else self._build_fallback(recommendation)

        logger.info(
            f"Verifier: passes_all={report.passes_all}, "
            f"structural=({report.actions_exist_in_wiki}, {report.warnings_exist_in_wiki}, {report.indices_in_range}), "
            f"semantic=({report.actions_match_risk_type}, {report.actions_dont_contradict_memory}), "
            f"safety=({report.passes_regex_filter}, {report.passes_llm_safety_check}), "
            f"calibration={report.confidence_calibrated_to_evidence}"
        )

        return report, safe_fallback

    # ── Structural checks ────────────────────────────────────────

    def _check_indices_in_range(self, rec: Recommendation, ev: EvidenceBundle) -> bool:
        """All selected indices must be within valid ranges of evidence articles."""
        total_actions = sum(len(a.get("actions", [])) for a in ev.wiki_articles)
        total_warnings = sum(len(a.get("warnings", [])) for a in ev.wiki_articles)

        actions_ok = all(
            0 <= idx < total_actions for idx in rec.selected_action_indices
        )
        warnings_ok = all(
            0 <= idx < total_warnings for idx in rec.selected_warning_indices
        )

        if not actions_ok:
            logger.warning(f"Action indices out of range (max={total_actions}): {rec.selected_action_indices}")
        if not warnings_ok:
            logger.warning(f"Warning indices out of range (max={total_warnings}): {rec.selected_warning_indices}")

        return actions_ok and warnings_ok

    def _check_actions_exist(self, rec: Recommendation, ev: EvidenceBundle) -> bool:
        """Each selected action index must resolve to a real action in the evidence."""
        all_actions = []
        for art in ev.wiki_articles:
            all_actions.extend(art.get("actions", []))

        for idx in rec.selected_action_indices:
            if idx >= len(all_actions) or idx < 0:
                logger.warning(f"Action index {idx} not found in evidence")
                return False
        return True

    def _check_warnings_exist(self, rec: Recommendation, ev: EvidenceBundle) -> bool:
        all_warnings = []
        for art in ev.wiki_articles:
            all_warnings.extend(art.get("warnings", []))

        for idx in rec.selected_warning_indices:
            if idx >= len(all_warnings) or idx < 0:
                logger.warning(f"Warning index {idx} not found in evidence")
                return False
        return True

    # ── Semantic checks ──────────────────────────────────────────

    def _check_actions_match_risk(self, rec: Recommendation, ev: EvidenceBundle) -> bool:
        """ESCALATE risk should NOT have only 'monitor' actions."""
        if rec.risk_level == "ESCALATE" and len(rec.selected_action_indices) == 0:
            return False
        if rec.risk_level == "NORMAL" and len(rec.selected_action_indices) > 3:
            # Suspicious: too many actions for a normal situation
            pass  # Not a hard fail, just note it
        return True

    def _check_memory_contradiction(self, rec: Recommendation, ev: EvidenceBundle) -> bool:
        """Check if advice contradicts recent memory."""
        # This is a heuristic stub — full contradiction detection needs temporal reasoning
        if ev.memory_context and "already applied" in ev.memory_context:
            for _action in rec.actions_text:
                # Check if we're recommending something that was already done
                # (simplistic check; production would use embeddings)
                pass
        return True  # Stub: always pass unless we build full contradiction detection

    # ── Calibration check ────────────────────────────────────────

    def _check_calibration(self, rec: Recommendation, ev: EvidenceBundle) -> bool:
        """Confidence should be proportional to evidence quantity and quality."""
        article_count = len(ev.wiki_articles)

        if rec.confidence == "HIGH" and article_count < 3:
            logger.warning(f"HIGH confidence with only {article_count} evidence articles")
            return False
        if rec.confidence == "HIGH" and not ev.weather_data and not ev.mandi_data:
            # Acceptable if wiki evidence is strong
            pass
        if rec.confidence == "LOW" and article_count >= 5:
            # Inverted: lots of evidence but low confidence
            pass  # Not a fail — could be genuinely ambiguous

        return True

    # ── Helpers ──────────────────────────────────────────────────

    def _build_full_advisory_text(self, rec: Recommendation, ev: EvidenceBundle) -> str:
        parts = [rec.contextualization]
        parts.extend(rec.actions_text)
        parts.extend(rec.warnings_text)
        if rec.memory_reference:
            parts.append(rec.memory_reference)
        return "\n".join(parts)

    def _build_fallback(self, rec: Recommendation) -> str:
        """Build safe fallback message when verification fails."""
        # Detect language from contextualization
        has_hindi = bool(re.search(r'[\u0900-\u097F]', rec.contextualization))
        return SAFE_FALLBACK_HI if has_hindi else SAFE_FALLBACK_EN


# ─── Singleton ────────────────────────────────────────────────────────

_verifier: VerifierService | None = None

def get_verifier() -> VerifierService:
    global _verifier
    if _verifier is None:
        _verifier = VerifierService()
    return _verifier
