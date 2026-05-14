# AgentAgri Product Pitch and Use Cases

Date: 2026-05-14

## 1. Product Pitch

AgentAgri is a farmer-first agricultural intelligence agent. A farmer talks to the Telegram agent in normal Hindi/English, shares crop photos or field updates, and opens a personal dashboard when the conversation needs more detail than chat can safely show.

The product is designed for smallholder farmers who need field-specific advice, not generic crop tips. It combines local farm memory, weather, market prices, crop calendar tasks, NDVI, finance, disease alerts, and evidence-backed advisories into one page that can be opened from Telegram.

Core promise:

- A farmer can continue the same conversation with field and crop context.
- Advice is grounded in evidence, prior memory, weather, market, and field state.
- Cluster alerts show what nearby farmers are reporting without exposing private farmer identity.
- The dashboard remains usable in low-connectivity situations through local browser cache.
- The agent treats farmer messages, old memory, retrieved pages, and cluster reports as untrusted context, so hidden instructions in those sources cannot override the safety and evidence rules.

## 2. Target Users

| User | Need | AgentAgri Response |
|---|---|---|
| Farmer | Understand what to do today for a crop/field | Telegram answer plus dashboard with next actions, forecast, memory, and map |
| Farmer with multiple fields | Keep conversations separate by field/crop | Durable conversation threads scoped by farmer, field, and crop cycle |
| Extension worker | Review disease/pest clusters before broadcast | Extension console with clusters, memory summaries, sources, eval, and impact graph |
| Demo tester | Verify the product without live integrations | Seed script creates demo farmer, fields, weather, mandi, memory, NDVI, clusters, wiki |
| Developer | Modify agent, dashboard, API, or data model | Developer spec mirrors this document with files, endpoints, schema, and test gates |

## 3. Expected Use Cases

### Use Case A: Farmer Opens Personal Dashboard from Telegram

1. Farmer sends `/demo`, `/start`, or `/dashboard` in Telegram.
2. Telegram sends a button linking to `/?mode=farmer&farmer_id=...` or `/?phone=...`.
3. Farmer opens the PWA dashboard.
4. The page shows:
   - weather/day-night background for the current field,
   - active crop and stage,
   - latest NDVI,
   - net season P&L,
   - nearby cluster count,
   - profile questions,
   - next best actions,
   - market and forecast signals,
   - map and memory tabs.

### Use Case B: Farmer Checks Nearby Cluster Risk

1. Farmer opens the Map tab.
2. Map shows the farmer's active field and nearby alert clusters.
3. Farmer moves zoom:
   - high zoom shows field-level cluster pins,
   - zooming out merges into village, tehsil, district, then state level.
4. Farmer taps a cluster.
5. Popup explains why the cluster matters for the farmer's current crop/field and warns not to copy another farmer's action without symptom match.

### Use Case C: Farmer Completes Profile

1. Dashboard shows Profile Coach.
2. Farmer answers missing questions such as farm size, irrigation source, soil test status, budget, preferred mandis, credit, and insurance.
3. Answers are saved to server through `PUT /api/farmers/{farmer_id}/profile`.
4. Latest dashboard payload is also cached on the device.
5. Future advisories can use this profile to personalize risk, budget, and action feasibility.

### Use Case D: Extension Worker Reviews Cluster

1. Extension worker opens `/?mode=extension`.
2. Clusters tab shows pending alert clusters.
3. Worker can:
   - approve and broadcast,
   - mark reviewed,
   - dismiss.
4. Sources, evals, memory, and impact tabs show operational readiness.

### Use Case E: Farmer Continues a Previous Field Conversation

1. Farmer asks a follow-up about a previous recommendation.
2. Agent loads the farmer, field, crop cycle, previous turns, prior evidence article IDs, and field memory.
3. Retrieval uses graph expansion when the query depends on earlier evidence.
4. Response explains whether the new answer changes, confirms, or depends on earlier advice.
5. The action impact network updates the consequence graph for future suggestions.

## 4. Product Distribution

### Demo Distribution

- GitHub repo: `https://github.com/Utkarsh-Sinha0/AgentAgri`
- Local web URL after startup: `http://127.0.0.1:8000/?mode=farmer`
- Extension console: `http://127.0.0.1:8000/?mode=extension`
- API docs in development: `http://127.0.0.1:8000/docs`

### Farmer Distribution

- Primary channel: Telegram bot.
- The bot exposes `/dashboard` and contextual dashboard buttons after advisories.
- Farmers do not need to install a native app for the demo.
- For phone-like use, the PWA can be installed to home screen from the browser.

### Extension Worker Distribution

- Extension workers use the same PWA with `?mode=extension`.
- Future production can issue role-based URLs and API keys.

## 5. Installation for Demo

```powershell
cd E:\Career\hackathon\AgentAgri
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
cd pwa
npm install
npm run build
cd ..
venv\Scripts\python scripts\seed_data.py
venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Optional Telegram:

```powershell
$env:TELEGRAM_BOT_TOKEN="your-token"
venv\Scripts\python -m app.bot.telegram_bot
```

Demo preparation:

| Need | Why It Matters |
|---|---|
| Seeded DB | Creates the farmer profile, field, crop, memory, NDVI, finance, and clusters used in the demo |
| Built PWA | FastAPI serves the static dashboard from `pwa/dist` |
| Ollama model | Required only for live advisory generation, not for seeded dashboard walkthrough |
| Telegram token | Required only if showing `/dashboard`, `/demo`, and chat buttons live |
| API key | Required only when showing production-style protected APIs |
| Browser cache | Demonstrates low-connectivity continuation after one successful dashboard load |
| Test run | Proves backend routes, retrieval fallback, profile validation, and dashboard payload are healthy |

## 6. Button and Control Guide

### Top Bar

| Button / Control | What It Does | User Outcome |
|---|---|---|
| Farmer | Opens farmer personal dashboard | Farmer sees field, weather, money, map, and memory |
| Extension | Opens extension worker console | Worker reviews clusters, sources, evals, and impact graph |
| API key | Stores `X-AgriMesh-API-Key` in browser storage | Allows protected production API calls |
| Refresh | Re-fetches all dashboard API calls | Updates server data without reloading browser |

### Farmer Tabs

| Tab | What It Shows | Primary Action |
|---|---|---|
| Overview | Weather, profile, next actions, forecast, market | Decide today's safest action |
| Fields | Field cards, active crops, NDVI trend, crop tasks | Inspect field health and pending work |
| Map | Farmer field plus cluster overlays | Understand nearby risk by zoom level |
| Money | Revenue, expense, net P&L, category flow | Track season economics |
| Memory | Conversations, memory atoms, action impacts | Continue with prior context |

### Extension Tabs

| Tab | What It Shows | Primary Action |
|---|---|---|
| Clusters | Pending disease/pest clusters | Approve, review, or dismiss |
| Memory | Living memory summaries by scale | Inspect field-to-region knowledge |
| Impact | Action consequence network | See how advice affects future suggestions |
| Sources | Evidence registry freshness | Check data trust before demo |
| Eval | Golden-query metrics | Verify model and safety readiness |

## 7. Visual and UX Direction

The refreshed UI takes inspiration from:

- Its Hover: animated icons that move with intent, used for the refresh control and CSS icon motion pattern.
- FarmDataViewer: map-first field management, field boundaries, observations, and offline field use.
- AcreMax 360: practical phone-first farming workflows, weather, GPS, field history, and installable PWA framing.
- FarmMind: GIS mapping, dashboards, weather/irrigation, and data visualization for agriculture.
- SaaS UI 2026 patterns: confidence over complexity, progressive disclosure, and showing the right data at the right time.
- Dashboard design guidance: metric strip in the first 80-120px of content, short labels, and one primary visual per metric.
- WCAG contrast guidance: readable text over variable light/dark weather backgrounds.

The UI deliberately keeps the first screen operational: identity, weather, active crop, NDVI, finance, alert count, and profile completion appear before deeper tabs. It is not a marketing landing page because the farmer's immediate task is decision support.

## 8. Demo Success Criteria

A demo is successful when:

- farmer dashboard opens from URL and Telegram button,
- profile questions can save,
- map cluster popup opens,
- zoom changes cluster level,
- extension mode still works,
- API endpoints return seeded data,
- PWA builds without error,
- backend tests pass,
- page remains readable in sunny, rainy, cloudy, storm, and night skins.
- protected demo mode rejects dashboard access unless the request includes a farmer identity,
- profile validation rejects impossible acreage, budget, and distance values before changing stored farmer data.
