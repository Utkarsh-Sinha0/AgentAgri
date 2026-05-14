# AgentAgri Features

## Farmer Dashboard

Personal farm command page with active crop, risk status, confidence, source count, latest advisory, readiness checks, and impact graph count.

Screenshot placeholder: `docs/screenshots/dashboard.png`

## Crop Analysis

Field-level crop analysis with active crop, stage, NDVI chart, crop tasks, and vision-analysis thumbnail when an observation image is available.

Screenshot placeholder: `docs/screenshots/crop-analysis.png`

## Market Prices

Mandi price rows and MSP context loaded from `/api/market-prices`, backed by seeded agmarknet-compatible data until live APIs are wired.

Screenshot placeholder: `docs/screenshots/market-prices.png`

## Weather

Forecast and history loaded from `/api/weather/forecast`, using the same service functions available to the agent tool path.

Screenshot placeholder: `docs/screenshots/weather.png`

## AI Advisor and Gemma Showcase

Shows the configured primary/fallback Gemma models, latest latency, confidence, retrieval path, tool-call visualization, citation list, and a reasoning summary. The UI intentionally shows a decision trace instead of hidden raw chain-of-thought.

Screenshot placeholder: `docs/screenshots/ai-advisor.png`

## History

Lists recent advisories with risk, confidence, actions, warnings, and timestamps from `/api/ai/showcase` or the farmer dashboard payload.

Screenshot placeholder: `docs/screenshots/history.png`

## Settings

Read-only runtime settings for health, environment, model metadata, and source registry status.

Screenshot placeholder: `docs/screenshots/settings.png`

## Backend Production Hardening

- FastAPI lifespan validates critical environment values before startup work.
- API errors use a consistent JSON envelope with status code and error code.
- Docker Compose development auth is explicitly disabled while production still fails closed.
- All Compose services include healthchecks.
- SQLAlchemy relationships and baseline Alembic schema include cascade or set-null foreign-key rules.
- Composite indexes cover common farmer, crop-cycle, date, source, cluster, and impact graph query paths.

