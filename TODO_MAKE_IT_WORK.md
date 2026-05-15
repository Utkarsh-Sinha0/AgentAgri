# TODO — Make It All Work

End-to-end checklist for a fresh clone to a running AgentAgri instance serving a farmer over Telegram. Items are ordered by dependency: do them top-to-bottom.

> Source of truth for the audit close-out and current test count: see [HANDOVER.md §21](HANDOVER.md). For deeper setup, see [docs/SETUP.md](docs/SETUP.md).

---

## 0. One-time prerequisites

- [ ] Install Docker Desktop (Windows/macOS) or docker-engine + docker-compose-plugin (Linux).
- [ ] Install Python 3.11+ (only needed if you plan to run tests / Alembic outside Docker).
- [ ] Install [Ollama](https://ollama.ai) on the host (not inside Docker — the container talks to the host).
- [ ] Pull at least one Gemma 4 model:
  ```bash
  ollama pull gemma4:2b      # fast, ~4 GB, recommended for first run
  # or gemma4:e2b / gemma4:e4b for higher quality
  ```
- [ ] Confirm Ollama is reachable: `curl http://localhost:11434/api/tags` returns JSON.

## 1. Clone & configure

- [ ] `git clone https://github.com/Utkarsh-Sinha0/AgentAgri.git && cd AgentAgri`
- [ ] `cp .env.example .env` (PowerShell: `copy .env.example .env`)
- [ ] Edit `.env`:
  - [ ] Set `OLLAMA_MODEL` to the tag you pulled (e.g. `gemma4:2b`).
  - [ ] Leave `DATABASE_URL` as the bundled SQLite URL for local; switch to Postgres for pilot.
  - [ ] Generate an API key if `AGRIMESH_REQUIRE_API_KEY=true`:
    `python -c "import secrets; print(secrets.token_urlsafe(32))"` → paste into `AGRIMESH_API_KEY`.
  - [ ] Telegram: paste your bot token from [@BotFather](https://t.me/BotFather) into `TELEGRAM_BOT_TOKEN`. Leave blank to skip the bot and run web only.

## 2. Database

- [ ] First boot creates `./data/agrimesh.db` automatically. To run migrations explicitly (recommended after pulling new commits):
  ```bash
  alembic upgrade head
  ```
- [ ] Seed the demo district (Munger, Bihar) and wiki:
  ```bash
  make seed         # or: python -m app.scripts.seed_all
  ```

## 3. Bring the stack up

- [ ] `docker-compose build`
- [ ] `docker-compose up` (add `-d` to detach)
- [ ] Smoke checks:
  - [ ] `curl http://localhost:8000/health` returns `{"status":"ok",...}`.
  - [ ] Open `http://localhost:8000` — farmer dashboard renders.
  - [ ] Telegram: `/start` to your bot replies with onboarding. `/demo` triggers the seeded flow.

## 4. Verify the audit close-out is wired

- [ ] Run the full suite (211 tests must pass):
  ```bash
  make test
  # or: pytest -q
  ```
- [ ] Run only the regression suite for the 15 audit findings:
  ```bash
  pytest tests/test_bug_regressions.py tests/test_conversation_impact.py -q
  ```
- [ ] If any test fails, see [HANDOVER.md §21](HANDOVER.md) — every fix maps to a numbered finding.

## 5. Operational sanity (do once before any demo)

- [ ] `/why <advisory_id>` over Telegram returns evidence with `source` and `trust` populated (MEDIUM #5).
- [ ] Send an ESCALATE-triggering photo; verifier must reject responses whose actions are monitor-only (LOW #9).
- [ ] Send a query with zero retrieval hits; check the DB has an Advisory row with `retrieval_path="none"` (LOW #10):
  ```bash
  sqlite3 data/agrimesh.db "select id, retrieval_path, confidence from advisories where retrieval_path='none' order by created_at desc limit 5;"
  ```
- [ ] Inspect ActionImpact rows for non-null `field_id` and `crop_cycle_id` (MEDIUM #8) and correct global `action_index` (HIGH #3):
  ```bash
  sqlite3 data/agrimesh.db "select advisory_id, action_index, field_id, crop_cycle_id from action_impacts limit 10;"
  ```

## 6. Production / pilot hardening (before letting real farmers in)

- [ ] Switch `DATABASE_URL` to Postgres and run `alembic upgrade head`.
- [ ] Set `APP_ENV=production` and `AGRIMESH_REQUIRE_API_KEY=true` with a fresh key.
- [ ] Lock `ALLOWED_ORIGINS` and `ALLOWED_HOSTS` to the real domain.
- [ ] Run `pip-audit` and `npm audit --omit=dev` in `pwa/`; both must be clean.
- [ ] Configure log shipping (default `LOG_LEVEL=INFO` writes to stdout — Docker captures).
- [ ] Set up a backup cron on `data/agrimesh.db` (or pg_dump on Postgres) at least daily.
- [ ] Rotate the Telegram bot token; revoke any token committed during dev.

## 7. Known follow-ups (not blocking, but next on the list)

- [ ] Live weather (IMD/OpenWeatherMap) — Phase 2 in [README §22](README.md).
- [ ] Live mandi feed from agmarknet.gov.in — Phase 2.
- [ ] WhatsApp Business API channel — Phase 2.
- [ ] Expand wiki from 11 → 50+ articles with expert review.
- [ ] Voice STT (Whisper or Gemma 4 native audio).

---

## If something is broken

| Symptom | First thing to check |
|---|---|
| `health` returns 503 | Is Ollama up on the host? `curl http://host.docker.internal:11434/api/tags` from inside the container. |
| Telegram bot silent | `TELEGRAM_BOT_TOKEN` set in `.env` and container restarted? `docker-compose logs telegram_bot`. |
| `alembic upgrade head` fails | Stale `data/agrimesh.db`. Back it up, delete it, re-run migrations + `make seed`. |
| Tests fail with `ImportError` | Wrong Python version. `python --version` must be 3.11+. |
| `npm audit` flags PWA | `cd pwa && npm install && npm audit fix`. |
| Verifier rejects everything | `USE_GRAMMAR_DECODING=1` requires Ollama with grammar support. Set to `0` to bypass for triage only — never in production. |
