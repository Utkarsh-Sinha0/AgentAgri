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

- `pwa/src/main.jsx`
  - farmer-first dashboard mode
  - weather-aware page skin
  - field/crop/NDVI, money, memory, and profile views
  - multi-scale cluster map overlays
  - local dashboard cache for offline resume
  - new Impact tab
  - action consequence cards
  - conversation graph readiness signal

- `app/services/farmer_dashboard.py`
  - dashboard aggregation service for farmer profile, fields, crop cycles, crop tasks, weather, mandi, finance, memory, advisories, cluster overlays, and sync metadata

- `app/bot/telegram_bot.py`
  - `/dashboard` command
  - dashboard URL button after advisory
  - clearer command buttons that describe what each action will do

## Sources Used

- World Bank Live, "Building AI Foundations From Farms to Future Economies", 2025.
- AgriRegion, arXiv:2512.10114, 2025.
- AgriGPT, arXiv:2508.08632, 2025.
- NASA POWER Daily API documentation.
- IMD API Management Platform.
- FAOSTAT API Developer Portal announcement, 2026.
- "Unlocking AI's Potential in Agriculture: The Critical Role of Data", arXiv:2603.23289, 2026.
