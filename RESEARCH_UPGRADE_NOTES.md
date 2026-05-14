# AgentAgri Evidence-Based Upgrade Notes

Date: 2026-05-14

## Research Signals Applied

1. Region-aware advisory is not optional for agriculture.
   - AgriRegion argues that advice can be scientifically valid in one place but harmful elsewhere because soil, climate, and regulation differ.
   - Applied change: the agent now injects farmer region, field, soil, irrigation, crop, and stage into memory context before template selection.

2. Retrieval should mix local context, dense retrieval, and graph reasoning.
   - AgriGPT describes a Tri-RAG pattern combining dense retrieval, sparse retrieval, and multi-hop knowledge graph reasoning.
   - Applied change: follow-up questions now route to graph retrieval using prior evidence article IDs from the durable conversation thread.

3. Smallholder AI needs persistent, localized, practical tools.
   - World Bank 2025 material emphasizes Small AI, local context, offline/mobile reach, and practical farmer decision support.
   - Applied change: farmer conversations are now durable per field/crop scope, so advice can continue after process restarts and remains field-specific.

4. India agriculture AI depends on machine-readable, cross-source data alignment.
   - 2026 India data-infrastructure research highlights spatial fragmentation, static formats, and timing mismatches with farm decisions.
   - Applied change: the source registry now includes IMD, FAOSTAT API developer portal, and product-research entries so data freshness and trust can be monitored.

5. Farmer trust improves when actions expose consequences.
   - Product implication from the evidence above: advice should not only say what to do, but what it changes, what it depends on, and how it affects future recommendations.
   - Applied change: every persisted advisory now builds an action impact network with expected result, dependencies, risks, metric deltas, and effects on prior/future suggestions.

6. Farmer-facing AI must expose context, not only chat answers.
   - World Bank's 2025 framing favors practical Small AI that fits real farmer workflows, while the India data-infrastructure review highlights that farm decisions fail when weather, market, soil, and spatial data remain fragmented.
   - Applied change: the PWA now defaults to a personalized farmer dashboard that combines profile completeness, active fields/crops, NDVI, weather, mandi, finance, advisories, memory, and sync status in one responsive page.

7. Spatial context should be multi-scale and farmer-relevant.
   - AgriRegion and India data-infrastructure findings both point to spatial heterogeneity: field-level advice should not be flattened into district-wide guidance.
   - Applied change: the dashboard map now shows field/village/tehsil/district/state cluster overlays. As zoom decreases, nearby farmer-risk overlays merge level by level, and the selected popup explains what that cluster means for the farmer's current field/crop.

8. Weather data should drive both advice and interface state.
   - NASA POWER and IMD-style data make weather a first-class operational input, not a passive label.
   - Applied change: the dashboard builds a weather skin from current forecast plus local IST day/night state, so the farmer page visually reflects sunny, cloudy, rain, storm, or night conditions while still keeping the UI readable.

9. Continuity requires server memory plus device-local cache.
   - For low-connectivity farmers, cloud-only state is brittle; for safety and personalization, device-only state is insufficient.
   - Applied change: the dashboard payload includes a server sync marker and the PWA stores the latest farmer dashboard in localStorage, allowing the farmer to resume context while keeping authoritative memory on the server.

10. Modern UI should increase confidence, not add decoration.
   - Its Hover's motion-first icons support using animation to communicate intent, while 2026 SaaS/dashboard guidance emphasizes calm density, progressive complexity, and the right data at the right moment.
   - Applied change: the PWA now uses an Its Hover-derived animated refresh icon, hover-intent icon states, visible form labels, stronger contrast surfaces, weather-safe readability, and a more polished map/metric/card system.

11. RAG and agent security need explicit trust boundaries.
   - OWASP Top 10 for LLM Applications 2025 identifies prompt injection, sensitive information disclosure, excessive agency, vector/embedding weaknesses, misinformation, and unbounded consumption as relevant risks for RAG and agent systems.
   - Applied change: the agent prompt now explicitly marks farmer messages, retrieved evidence, memory, and conversation history as untrusted factual context, not executable instructions. Protected dashboard access now requires farmer identity when API-key auth is enabled.

12. AI risk management should be governable and testable.
   - NIST AI RMF and the GenAI Profile emphasize mapping, measuring, managing, and documenting risks rather than trusting model behavior implicitly.
   - Applied change: the developer spec now records the retrieval/reasoning contract, security checks, dependency checks, demo boundaries, and local verification commands.

13. Agricultural RAG systems should use curated package-of-practices style sources and domain evaluation.
   - A 2026 Journal of Agricultural Engineering RAG advisory framework compares LLMs on agronomic guidance over package-of-practices documents, reinforcing the need for evidence-grounded domain retrieval rather than generic chat.
   - Applied change: the existing wiki/evidence registry remains the source of action indices, and empty keyword retrieval now safely returns no evidence instead of generating invalid SQL.

## Implemented Modules

- `app/models_memory.py`
  - `ConversationThread`
  - `ConversationTurn`
  - `ActionImpact`

- `app/services/conversation.py`
  - follow-up detection
  - durable thread lookup
  - compact conversation context
  - previous evidence IDs for graph expansion
  - deterministic action impact graph creation

- `app/services/agent.py`
  - personalized farm context injection
  - durable conversation context injection
  - previous evidence reuse for follow-up graph retrieval
  - conversation and impact persistence after advisory generation

- `app/main.py`
  - `/api/farmers/{farmer_id}/conversation`
  - `/api/advisories/{advisory_id}/impact-network`
  - `/api/impact-network`
  - `/api/farmer-dashboard`
  - `/api/farmers/{farmer_id}/profile`
  - stats now include conversation and impact counts
  - farmer profile input bounds
  - invalid content-length rejection
  - production protected dashboard requires farmer identity

- `app/database.py`
  - SQLite additive schema repair is now called during startup instead of existing as unused helper code

- `app/services/agent.py`
  - removed unused evidence-card parameter

- `app/services/cluster.py`
  - `radius_km` now filters same-window observations by approximate field distance when coordinates exist

- `pwa/src/main.jsx`
  - farmer-first dashboard mode
  - weather-aware page skin
  - field/crop/NDVI, money, memory, and profile views
  - multi-scale cluster map overlays
  - local dashboard cache for offline resume
  - visible input labels and button titles for clearer tester interaction
  - new Impact tab
  - action consequence cards
  - conversation graph readiness signal

- `pwa/src/styles.css`
  - contrast-safe weather skins
  - professional dashboard surfaces, tab states, hover states, map pins, and responsive layout

- `pwa/src/components/icons/hover-refresh-icon.jsx`
  - animated refresh icon adapted from Its Hover registry

- `app/services/farmer_dashboard.py`
  - dashboard aggregation service for farmer profile, fields, crop cycles, crop tasks, weather, mandi, finance, memory, advisories, cluster overlays, and sync metadata
  - bounded profile list normalization

- `app/services/retrieval.py`
  - empty keyword retrieval guard

- `app/utils/ollama_client.py`
  - explicit untrusted-context instruction for farmer input, memory, conversations, and retrieved evidence
  - `max_tokens` is now passed to Ollama as `num_predict`

- `app/services/verifier.py`
  - deterministic memory contradiction check now flags repeated actions already recorded in field memory
  - removed no-op pass branches from semantic/calibration checks

- `tests/test_api_security.py`
  - API-key dashboard identity gate
  - invalid profile numeric rejection
  - invalid content-length rejection

- `tests/test_retrieval.py`
  - empty keyword query regression coverage

- `app/bot/telegram_bot.py`
  - `/dashboard` command
  - dashboard URL button after advisory
  - clearer command buttons that describe what each action will do

## Code and Security Audit Results

| Check | Result |
|---|---|
| `venv\Scripts\python -m pip check` | Passed, no broken requirements |
| `venv\Scripts\python -m pip_audit -r requirements.txt` | No known vulnerabilities reported |
| `npm audit --omit=dev` | 0 vulnerabilities |
| `venv\Scripts\python -m compileall -q app tests scripts` | Passed |
| `venv\Scripts\python -m ruff check app tests` | Passed |
| `venv\Scripts\python -m vulture app scripts tests --min-confidence 80` | Passed after removing unused variables and wiring SQLite schema sync |
| Focused security/retrieval tests | Passed |
| `npm run build` | Passed |
| debt scan over app, pwa/src, scripts, tests, and docs | No stale marker, placeholder, or unused-code patterns found |

Remaining demo-phase boundaries are documented in `DEVELOPER_SPEC_AND_TESTER_README.md`: seeded/live provider split, in-memory rate limit for local demo, SQLite default, and uneven coverage for Telegram/Ollama/external-provider modules.

## Sources Used

- World Bank Live, "Building AI Foundations From Farms to Future Economies", 2025.
- AgriRegion, arXiv:2512.10114, 2025.
- AgriGPT, arXiv:2508.08632, 2025.
- Journal of Agricultural Engineering, "Empowering farmers with artificial intelligence: a retrieval-augmented generation based large language model advisory framework", 2026.
- OWASP Top 10 for LLM Applications v2025.
- NIST AI Risk Management Framework and NIST AI 600-1 Generative AI Profile.
- NASA POWER Daily API documentation.
- IMD API Management Platform.
- FAOSTAT API Developer Portal announcement, 2026.
- "Unlocking AI's Potential in Agriculture: The Critical Role of Data", arXiv:2603.23289, 2026.
- Its Hover, animated icon registry and project documentation, 2026.
- FarmDataViewer field mapping and task management product documentation.
- AcreMax 360 farm mapping and installable app product documentation.
- FarmMind GIS mapping feature documentation.
- SaaSUI 2026 UI trend analysis.
- Dashboard design pattern guidance for metric strips and navigation.
- WCAG 2.2 contrast guidance for readable text over variable backgrounds.
