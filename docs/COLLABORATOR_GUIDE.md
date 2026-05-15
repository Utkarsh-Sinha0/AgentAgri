# Collaborator Guide — AgentAgri

> Welcome. This is the only doc you need to read first. After this, the order is: [SETUP.md](SETUP.md) → [ARCHITECTURE.md](ARCHITECTURE.md) → [WORKFLOW.md](WORKFLOW.md) → [FEATURES.md](FEATURES.md) → pick a task below.

---

## Before you touch anything

1. **Read `LICENSE`** (in the repo root). By cloning this repo you have agreed to its terms, including the NDA clause. Take it seriously — this is intended for the Kaggle Gemma 4 Good Hackathon and a possible commercial pilot afterwards.
2. **The product specs are locked.** Every feature listed in [FEATURES.md](FEATURES.md) ships as described. We are improving quality, polish, and coverage — not changing what the agent does.
3. **Don't push directly to `main`.** Always work on a branch named `<your-initials>/<short-topic>` (e.g. `sm/eval-tuning`).
4. **Don't skip git hooks.** No `--no-verify`. If a hook fails, fix the underlying issue.
5. **Ask before destructive actions** — `git reset --hard`, force-push, dropping the DB, deleting eval files, etc.

---

## Setup checklist (1–2 hours, one-time)

Follow [SETUP.md](SETUP.md) and [TODO_MAKE_IT_WORK.md](../TODO_MAKE_IT_WORK.md) literally, top to bottom. The short version:

```bash
git clone <repo-url> && cd AgentAgri
python -m venv venv && venv\Scripts\activate          # Windows
# or: python -m venv venv && source venv/bin/activate # Mac/Linux
pip install -r requirements.txt

# Install Ollama (https://ollama.ai) — must run on your host, not in Docker
ollama pull gemma4:e2b-it-q4_K_M

cp .env.example .env                                   # then edit per SETUP.md
alembic upgrade head
python -m app.scripts.seed_all                         # or: make seed

# Run tests — all 211 must pass before you start any task
pytest -q

# Boot the stack
docker-compose up                                      # or: uvicorn app.main:app --reload
```

If `pytest -q` shows anything other than 211 passed, **stop** and message Utkarsh before continuing. The test suite is the contract.

---

## Your ownership areas

Pick one to start. Don't spread across all six at once — pick the one that interests you most, finish a measurable slice, ship it, then move to the next.

### Area 1 — Telegram bot UX & conversational flows

**What's in scope:**
- Onboarding flow polish (district → village → crop → sowing date). The bot already does this; make it feel less robotic. Add inline keyboards for district / crop selection instead of free-text where possible.
- Error-recovery messages — when retrieval returns nothing, when MCP servers are down, when the model takes >60 s. Currently we have generic strings. Replace them with empathetic Hindi messages.
- Implement `/help`, `/myfields`, `/history` (last 5 advisories), `/feedback <advisory_id> <1-5>`.
- Bhojpuri language toggle (feature flag `ENABLE_BHOJPURI` exists in `.env.example`). When the farmer's district is Munger / Bhagalpur, offer Bhojpuri rendering.

**Where you'll work:**
- `app/bot/telegram_bot.py` — all handlers
- `app/services/farmer_dashboard.py` — anything that aggregates farmer data
- New: `app/bot/keyboards.py` if you add inline keyboards

**What "good" looks like:**
- A farmer with zero literacy can complete onboarding in under 90 seconds with mostly taps, no typing
- Every error path has a Hindi message that tells the farmer what to do next ("तुरंत फिर कोशिश करें" / "थोड़ी देर बाद कोशिश करें")
- `/feedback 5` after an advisory persists into `ActionImpact.farmer_feedback`
- New tests: at least 5 added to `tests/test_telegram_flows.py`

**Skills you'll use:** Python async, `python-telegram-bot` library, basic Hindi/Devanagari handling, SQLAlchemy queries.

---

### Area 2 — Eval expansion + accuracy tuning ⭐ (highest impact)

**What's in scope:**
- Run the full 200-case eval on `gemma4:e2b-it-q4_K_M`: `python -m app.eval_synthetic`
- For each category (diagnosis, followup, safety_adversarial, escalate, memory_recall, no_evidence, scheme_market, calibration), open the failing cases in `evals/synthetic_results_<ts>.json` and find the pattern.
- Tune the prompts in `app/utils/ollama_client.py` (specifically `TEMPLATE_SELECTION_PROMPT` and `INTENT_CLASSIFICATION_PROMPT`) — one variable at a time, re-run a 20-case slice to verify lift before committing.
- Convert every distinct failure mode into a regression test in `tests/test_eval_regressions.py` (new file). When the eval suite catches something, the regression suite should catch it forever.

**Where you'll work:**
- `app/utils/ollama_client.py` — prompts only
- `app/services/verifier.py` — add semantic checks for newly discovered failure modes
- `app/eval_synthetic.py` — add new metrics (e.g. action-precision, follow-up coherence)
- `tests/test_eval_regressions.py` — new

**What "good" looks like:**
- `risk_match_rate` ≥ 70% on diagnosis (currently ~30%)
- `safety_pass_rate` ≥ 95% (currently 90%)
- `verifier_pass_rate` stays at 100% — never sacrifice safety for accuracy
- A short doc `docs/eval_findings_<date>.md` with: what you changed, before/after numbers, hypotheses for remaining gaps

**Skills you'll use:** prompt engineering, careful A/B reasoning, statistics literacy (it's easy to fool yourself with 5-case samples), Python.

**Watch out:** each full 200-case run takes ~50 minutes on CPU. Plan accordingly — run small slices (`--limit 20 --categories diagnosis`) while iterating.

---

### Area 3 — Frontend PWA dashboard

**What's in scope:**
- The PWA lives in `pwa/`. It's the extension-worker view: list of farmers, recent advisories, cluster alerts, intervention recommendations.
- Add views: per-farmer history, cluster detail, evidence card display, accept/reject extension-worker action buttons.
- Add a small "explain this advisory" UI that calls the existing `/why/<advisory_id>` endpoint and renders the evidence cards.

**Where you'll work:**
- `pwa/` — React + Vite (check `pwa/package.json`)
- `app/api/v1/` — add endpoints if the frontend needs new shapes (don't reshape existing ones — extend)
- `app/services/farmer_dashboard.py` — aggregation logic stays here

**What "good" looks like:**
- A district officer can open the dashboard, see the 5 most-active clusters today, click into one, see the affected farmers, and broadcast a Hindi advisory to all of them
- Works on a low-end Android phone over 3G
- `npm audit` clean

**Skills you'll use:** React 18, TypeScript, Tailwind (already in `pwa/`), basic REST consumption.

---

### Area 4 — STT/TTS voice integration (Phase 2)

**What's in scope:**
- This is a future feature. Read [ROADMAP_STT_TTS.md](ROADMAP_STT_TTS.md) for the integration plan.
- Phase A: voice notes → text (STT). Phase B: bot reply → voice (TTS). Phase C: end-to-end voice loop.
- Evaluate Sarvam AI's Hindi/Bhojpuri APIs and at least two open-source alternatives (faster-whisper-large-v3, Indic-Conformer). Pick on accuracy + latency + cost.
- Add feature flags: `ENABLE_VOICE_STT`, `USE_GEMMA_AUDIO` (already in `.env.example`).

**Where you'll work:**
- New: `app/services/stt.py`, `app/services/tts.py`
- `app/bot/telegram_bot.py` — voice message handler
- `app/services/degradation.py` — voice fallback to text when STT fails

**What "good" looks like:**
- A farmer can send a 30-second Hindi voice note and get a voice reply in under 90 seconds
- Word error rate (WER) under 20% on the Munger Hindi dialect
- Cost per voice turn under ₹0.50 (back-of-envelope, document your math)
- New tests in `tests/test_voice_flows.py`

**Skills you'll use:** API integration, audio handling (pydub / ffmpeg), latency benchmarking, comparative evaluation.

---

### Area 5 — Wiki expansion (11 → 50+ articles)

**What's in scope:**
- The wiki under `wiki/articles/` is the knowledge base the model is grounded in. We have 11 articles. Target: 50 across rice, wheat, maize, mustard, pulses, common pests/diseases, and major schemes.
- Each article needs: title (en + hi), summary, full content, actions (indexed list), warnings (indexed list), applicable_crops, applicable_stages, topic_tags, source citations.
- Source from: ICAR publications, Krishi Vigyan Kendra leaflets, state agri department PDFs. Cite every claim.

**Where you'll work:**
- `wiki/articles/` — one markdown file per article
- `app/services/demo_seed.py` — re-seed after adding
- `tests/test_retrieval.py` — add per-article retrievability tests

**What "good" looks like:**
- 50+ articles, each with at least 3 citations to authoritative sources
- Retrieval test: each new article must be retrievable for at least one realistic farmer query
- A short `wiki/CONTRIBUTING.md` documenting your article-writing checklist for the next person

**Skills you'll use:** agronomic research, Hindi technical writing, basic SQL (to verify seed loaded), patience.

**Watch out:** don't paraphrase scientific PDFs into chemical dosages. Always defer to "consult the label" / "ask your Krishi Vigyan Kendra."

---

### Area 6 — Observability, monitoring, and regression growth

**What's in scope:**
- Wire up Grafana dashboards for: request latency (p50/p95), Ollama call latency, retrieval-path distribution (fast vs graph vs none), verifier pass rate over time, error rate per category.
- Add structured logging tags (`farmer_id`, `advisory_id`, `retrieval_path`) so we can join logs to advisories in postmortems.
- Grow the test suite from 211 → 300+ by translating each eval failure (Area 2) into a frozen regression test.

**Where you'll work:**
- `app/main.py` — middleware for request timing
- `app/utils/ollama_client.py` — log every call's latency + token count
- New: `docs/RUNBOOK.md` — what to do when latency spikes / errors climb / disk fills
- `tests/test_bug_regressions.py` — append to this

**What "good" looks like:**
- A Grafana board the demo-day operator can glance at to know the bot is healthy
- Logs from any single advisory can be retrieved by `advisory_id` in one grep
- Test suite grows by at least 30 tests, each tied to a real eval-discovered failure

**Skills you'll use:** Grafana / Prometheus basics, structured logging, regression testing discipline.

---

## How to work day-to-day

1. **Pick a task.** Open an issue describing what you'll do and your success criteria (look at the "What good looks like" bullets above).
2. **Branch.** `git checkout -b <initials>/<topic>`.
3. **Small commits.** One logical change per commit. Run `pytest -q` before each commit. Commit messages: `<scope>: <imperative summary>` (e.g. `bot: add inline keyboard for district selection`).
4. **Run the eval slice that matters.** If you touched a prompt, run `python -m app.eval_synthetic --limit 20 --categories <relevant>`. Paste the before/after summary in your PR description.
5. **Open a PR.** Title = imperative. Description = (a) what you changed (b) why (c) before/after numbers (d) screenshots if UI (e) any new tests.
6. **Address review.** I will be blunt. It's not personal — it's the only way to ship safely.
7. **Merge.** Squash-merge into `main`. Delete branch.

---

## How to ask for help

- **Quick question:** Telegram / WhatsApp DM is fine.
- **Stuck for >2 hours:** open a discussion in the GitHub repo with: what you tried, what you observed, what you expected, what files you looked at. The 2-hour rule is firm — don't burn a day in silence.
- **Suspected bug in shipped code:** open an issue with the failing test, then attempt the fix on a branch. Don't ship a fix without a regression test that proves it.

---

## What to never do

- Push to `main` directly
- Bypass git hooks (`--no-verify`, `--no-gpg-sign`)
- Commit a `.env` file with real secrets (the `.gitignore` blocks this — but double-check)
- Add a new feature that isn't in [FEATURES.md](FEATURES.md) without asking first
- Change the model identity, the verifier's 4 lines, or the citation-by-index discipline (these are the agent's safety story)
- Ship without running the full `pytest -q`
- Paraphrase a chemical dosage. Always say "follow label instructions"
- Share screenshots of farmer data publicly. Anonymise everything before any external post

---

## What "in total control" means

By the end of week 2, you should be able to:
- Explain the 14-step request lifecycle ([WORKFLOW.md](WORKFLOW.md)) to someone else, in your own words
- Point at any one of the 211 tests and explain which production code path it covers
- Diagnose any of the 6 failure modes in WORKFLOW.md's troubleshooting table from logs alone
- Run a full eval, read the summary, identify the worst category, and propose a fix without my prompting

That's the bar. The docs are here to get you there. If anything in them is unclear, that's a doc bug — file an issue.

---

Welcome aboard.
— Utkarsh
