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

import asyncio
import re
from dataclasses import dataclass, field
from datetime import timedelta

from loguru import logger

from app.utils.safety import SAFE_FALLBACK_EN, SAFE_FALLBACK_HI, full_safety_check
from app.utils.time import utc_now

# Semantic-similarity check (Bug 6) — uses the same BGE-M3 instance that
# retrieval already loads. We never trigger the lazy load from here; if the
# embedder hasn't been initialised yet we silently fall back to the keyword
# heuristic so the verifier stays cheap in unit tests.
try:
    from app.services import retrieval as _retrieval_mod
except Exception:  # pragma: no cover - import-time defensiveness
    _retrieval_mod = None  # type: ignore[assignment]

_SEMANTIC_CONTRADICTION_THRESHOLD = 0.80

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
    universal_kb_docs: list[dict] = field(default_factory=list)


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

        # Detect response type so we can relax structural+calibration checks
        # for legitimate non-advisory responses (safety redirects, escalations
        # with no actionable indices). These were failing verifier purely
        # because they have zero wiki-indexed actions.
        is_safety_redirect = self._is_safety_redirect(recommendation, evidence)

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
        report.actions_dont_contradict_memory = await self._check_memory_contradiction(
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
        # For safety-redirect responses (e.g. "don't spray 5x, consult KVK"),
        # the agent legitimately picks no wiki actions and routes to a human.
        # Don't fail those purely on structural/calibration grounds — safety
        # signal is what matters there.
        if is_safety_redirect:
            report.passes_all = all([
                report.passes_regex_filter,
                report.passes_llm_safety_check,
            ])
            report.details["response_type"] = "safety_redirect"
        else:
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

    # ── Response-type detection ──────────────────────────────────

    @staticmethod
    def _is_safety_redirect(rec: Recommendation, ev: EvidenceBundle) -> bool:
        """A safety redirect picks no/few wiki actions and points to a human.

        Heuristic: no selected_action_indices AND (should_escalate OR the
        contextualization mentions an extension worker / call center / label).
        """
        if rec.selected_action_indices:
            return False
        if rec.should_escalate:
            return True
        ctx = (rec.contextualization or "").lower()
        redirect_markers = (
            "extension worker", "kisan call", "krishi vigyan", "kvk",
            "consult", "do not mix", "do not spray",
            "विस्तार", "किसान कॉल", "कृषि विज्ञान", "लेबल", "नहीं छिड़कें",
        )
        return any(m in ctx for m in redirect_markers)

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
        """ESCALATE risk must carry at least one *active* action.

        Earlier the check only required len(actions) >= 1 for ESCALATE, so a
        recommendation like ["Monitor the field daily"] could escalate without
        proposing any intervention. ESCALATE is the verifier's "hand this to a
        human or do something material" signal — pure observation actions are
        a verifier failure and should fall back to the safe template.
        """
        if rec.risk_level == "ESCALATE":
            if len(rec.selected_action_indices) == 0:
                return False
            monitor_only_tokens = (
                "monitor", "scout", "inspect", "observe", "watch",
                "देख", "जांच", "निरीक्षण", "अवलोकन",
            )
            if rec.actions_text and all(
                any(tok in (a or "").lower() for tok in monitor_only_tokens)
                for a in rec.actions_text
            ):
                logger.warning(
                    "ESCALATE rejected: all selected actions are monitor-only "
                    f"({rec.actions_text})"
                )
                return False
        if rec.risk_level == "NORMAL" and len(rec.selected_action_indices) > 3:
            logger.info("NORMAL risk advisory includes multiple actions; allowed but worth monitoring")
        return True

    async def _check_memory_contradiction(
        self, rec: Recommendation, ev: EvidenceBundle
    ) -> bool:
        """Check if advice contradicts recent memory.

        Two-stage check (Bug 6):
          1. Keyword precondition — memory must contain a completion marker
             (bilingual: "already applied", "पहले", "कर चुके" …). If no
             completion signal is present, there is nothing to contradict.
          2. Semantic similarity — if BGE-M3 is already loaded, embed each
             proposed action and the memory context and flag a contradiction
             when cosine similarity exceeds 0.80. Falls back to the original
             keyword-overlap heuristic when the embedder hasn't been loaded
             (we never force the 500 MB load from the verifier).
        """
        memory = ev.memory_context.lower()
        if not memory:
            return True

        done_markers = (
            "already applied",
            "already done",
            "already used",
            "already sprayed",
            "already sown",
            "done earlier",
            "used earlier",
            "applied yesterday",
            "applied last week",
            "पहले",
            "कर चुके",
            "लगा चुके",
            "छिड़क चुके",
            "डाल चुके",
        )
        if not any(marker in memory for marker in done_markers):
            return True

        if not rec.actions_text:
            return True

        # Stage 2a — semantic similarity, only if embedder is already warm.
        contradiction = await self._semantic_action_repeated(
            rec.actions_text, ev.memory_context
        )
        if contradiction is not None:
            if contradiction:
                logger.warning(
                    "Semantic check: proposed action overlaps with completion "
                    "signal in memory (cos_sim >= "
                    f"{_SEMANTIC_CONTRADICTION_THRESHOLD})"
                )
                return False
            return True

        # Stage 2b — keyword overlap fallback.
        action_terms = _important_terms(" ".join(rec.actions_text))
        overlapping_terms = [term for term in action_terms if term in memory]
        if overlapping_terms:
            logger.warning(
                f"Recommendation may repeat an already recorded action: {overlapping_terms[:5]}"
            )
            return False
        return True

    @staticmethod
    async def _semantic_action_repeated(
        actions: list[str], memory_context: str
    ) -> bool | None:
        """Return True/False if a semantic check ran, or None to skip.

        We deliberately do *not* call ``get_embedder()`` here — that would
        trigger the 500 MB lazy load from inside the verifier hot path and
        from unit tests that don't need it. Instead we peek at the module
        global; if it's already initialised we use it, otherwise we return
        ``None`` so the caller can fall back to keyword matching.
        """
        if _retrieval_mod is None:
            return None
        embedder = getattr(_retrieval_mod, "_embedder", None)
        if embedder is None:
            return None
        try:
            from sentence_transformers.util import cos_sim  # type: ignore
        except Exception:
            return None

        try:
            action_text = " ".join(a.strip() for a in actions if a and a.strip())
            if not action_text or not memory_context.strip():
                return False

            # Run encode + similarity in a worker thread — encode is CPU-bound.
            def _score() -> float:
                vecs = embedder.encode(
                    [action_text, memory_context],
                    convert_to_tensor=True,
                    normalize_embeddings=True,
                )
                sim = cos_sim(vecs[0], vecs[1])
                return float(sim.item() if hasattr(sim, "item") else sim[0][0])

            similarity = await asyncio.to_thread(_score)
            return similarity >= _SEMANTIC_CONTRADICTION_THRESHOLD
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"Semantic contradiction check failed, falling back: {exc}")
            return None

    # ── Calibration check ────────────────────────────────────────

    def _check_calibration(self, rec: Recommendation, ev: EvidenceBundle) -> bool:
        """Confidence should be proportional to evidence quantity AND recency.

        Rules (Bug 5):
        * HIGH requires (>=3 wiki articles) AND ((>=5 memory atoms) OR
          (memory evidence within the last 14 days)). This gates HIGH on
          fresh evidence so stale memories alone don't inflate confidence.
        * MEDIUM requires >=1 wiki article. Anything less is downgraded.
        * LOW / ESCALATE pass calibration unconditionally (LOW is already
          conservative; ESCALATE just routes to a human expert).
        """
        article_count = len(ev.wiki_articles)
        memory = ev.memory_context or ""

        # Atom count signal — _load_memory_context emits one "  - [<atom_type>]"
        # line per atom. Count those lines, not the literal word "atom_type"
        # (which never appears in the rendered text).
        atom_count = self._count_memory_atoms(memory)

        # Recency signal — any YYYY-MM in memory within the last 14 days.
        has_recent_evidence = self._memory_has_recent_evidence(memory, days=14)

        if rec.confidence == "HIGH":
            if article_count < 3:
                logger.warning(
                    f"HIGH confidence rejected: only {article_count} wiki articles"
                )
                return False
            if atom_count < 5 and not has_recent_evidence:
                logger.warning(
                    f"HIGH confidence rejected: {atom_count} atoms and no "
                    f"evidence within last 14 days"
                )
                return False
            return True

        if rec.confidence == "MEDIUM" and article_count < 1:
            logger.warning("MEDIUM confidence rejected: 0 wiki articles")
            return False

        return True

    @staticmethod
    def _memory_has_recent_evidence(memory: str, days: int = 14) -> bool:
        """Return True if memory_context references a date within `days`.

        Matches ISO-like YYYY-MM and YYYY-MM-DD tokens, the formats used by
        memory.retrieve_memory_context when it emits ``event_at`` timestamps.
        Tolerates malformed dates by skipping them rather than raising.
        """
        if not memory:
            return False
        now = utc_now()
        cutoff = now - timedelta(days=days)
        for token in re.findall(r"\b(\d{4})-(\d{2})(?:-(\d{2}))?\b", memory):
            year, month, day = token
            try:
                y = int(year)
                m = int(month)
                d = int(day) if day else 1
                if not (1 <= m <= 12 and 1 <= d <= 31):
                    continue
                ref = now.replace(year=y, month=m, day=d, hour=0, minute=0,
                                  second=0, microsecond=0)
            except ValueError:
                continue
            # Future-dated tokens (typos, schema drift) shouldn't count as recent.
            if ref > now:
                continue
            if ref >= cutoff:
                return True
        return False

    @staticmethod
    def _count_memory_atoms(memory: str) -> int:
        """Count atoms in a rendered memory_context.

        `agent._load_memory_context` emits one ``  - [<atom_type>]`` line per
        atom (with an optional ``[date]`` after it). Match that prefix so the
        counter tracks the real rendered output rather than a literal word.
        """
        if not memory:
            return 0
        return len(re.findall(r"(?m)^\s*-\s*\[[^\]]+\]", memory))

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


def _important_terms(text: str) -> list[str]:
    """Extract coarse action terms for deterministic memory contradiction checks."""
    stopwords = {
        "about",
        "after",
        "apply",
        "before",
        "check",
        "consult",
        "field",
        "follow",
        "label",
        "monitor",
        "spray",
        "today",
        "with",
        "your",
    }
    terms = []
    for term in re.findall(r"[a-zA-Z][a-zA-Z_-]{3,}", text.lower()):
        if term not in stopwords and term not in terms:
            terms.append(term)
    return terms
