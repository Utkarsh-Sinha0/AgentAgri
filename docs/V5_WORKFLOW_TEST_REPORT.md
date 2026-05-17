# AgentAgri V5 Workflow And Optimization Report

Date: 2026-05-17

## Scope

This report covers the current implementation after Sprints 1-10, including the V5 additions:

- voice-first onboarding and voice/photo multimodal Telegram flow
- universal KB prompt grounding for playbooks, MSP, schemes, insurance, and storage
- crop-cycle stage advancement, sell/store decisions, and next-crop rotation
- district/state/national aggregate memory with privacy gates
- farmer data transparency through `/mydata` and soft redaction through `/forgetme`
- backend complexity optimizations using `complexity-optimizer`

The PWA dashboard UI was intentionally not changed.

## Sprint Status

| Sprint | Status | Evidence |
| --- | --- | --- |
| 1-6 | Implemented before this pass | Existing tests cover auth, retrieval, grammar, evidence citation, memory, Telegram flows, outbreak alerts, dashboard, MCP startup. |
| 7 | Implemented | Voice registration, photo+voice pairing, `/start` thread buttons, universal KB prompt injection. |
| 8 | Implemented | `crop_cycle.advance_stage`, sell-intent routing, storage rule, `/closecycle` rotation suggestion. |
| 9 | Implemented | multi-scope coarsening and `cluster_intel` aggregate queries with k-anonymity gate. |
| 10 | Implemented | `MemoryAtom.redacted`, `/mydata`, `/forgetme`, and documented device/server partition. |

## Real-Data Boundary

The app uses configured live APIs when keys are present and real local seed files otherwise. The smoke runner does not mock services; it exercises:

- configured DB
- seed demo farmer and memory palace
- actual weather service path
- actual mandi/MSP service path
- actual sell/storage logic
- actual dashboard payload builder
- actual coarsening and rotation services

Telegram and Sarvam voice require a live Telegram chat/audio note and configured credentials, so automated local smoke verifies the app wiring but does not impersonate a real Telegram client.

## Complexity Optimizations Applied

| Area | Before | After |
| --- | --- | --- |
| Dashboard field payload | Per-field queries for active crop, NDVI, tasks, threads; per-thread queries for turns. | Bulk field/cycle/NDVI/task/thread/turn fetch with in-memory grouping. |
| Pattern discovery graph edges | Two `WikiArticle` queries per co-occurring pair. | One bulk article fetch, then O(1) lookup per pair. |
| Proactive weather alerts | Repeated farmer query for each humid forecast day. | One vulnerable-farmer query reused for every humid day. |
| Proactive cluster alerts | Farmer query per alert cluster. | Bulk farmers by affected district/tehsil scopes, grouped in memory. |

## Verification Commands

```powershell
venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
venv\Scripts\python.exe -m ruff check app\bot\telegram_bot.py app\services\agent.py app\services\voice.py app\services\market_intel.py app\services\memory.py app\services\cluster_intel.py app\services\crop_cycle.py app\services\rotation.py app\services\storage_decision.py app\services\weather.py app\services\farmer_dashboard.py app\services\pattern_discovery.py app\services\proactive.py app\utils\ollama_client.py scripts\reflect_living_memory.py scripts\smoke_v5_real_data.py alembic\versions\0005_memory_atom_redacted.py tests\test_v5_services.py
venv\Scripts\python.exe scripts\smoke_v5_real_data.py
git diff --check
```

## Remaining Manual Gate

Before a public demo, run one live Telegram/Sarvam check:

1. New farmer sends one Hindi voice note with name, village, district, crop.
2. Bot registers, confirms by voice, and creates field/crop records.
3. Farmer sends crop photo without caption, then sends a voice question.
4. Bot replies in the farmer language and preserves the conversation thread.

This gate needs real Telegram chat access and Sarvam credentials; it is not represented by mock tests.
