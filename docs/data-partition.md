# AgentAgri Data Partition

This is the V5 privacy contract for the farmer-first Telegram flow.

## Farmer Device / Telegram Visible

Farmers can see only their own raw memory:

- observations, voice intents, photo notes, advisories, outcomes, field insights
- their active crop, field, stage, and conversation thread state
- their own `/mydata` digest

`/forgetme` soft-redacts farmer-owned raw `MemoryAtom` rows by setting `redacted=true`, replacing the summary, and clearing details. Future retrieval and coarsening skip redacted atoms.

## Server Only

Server-side aggregate intelligence is never exposed as raw farmer rows:

- village, tehsil, district, state, and national `MemorySummary` rows
- cluster intelligence from at least 3 distinct farmers
- anonymous stage distributions and pest/disease pressure summaries

The raw `farmer_id`, `field_id`, GPS, and free-text details of other farmers must not be returned to another farmer. Aggregate summaries may continue to exist after `/forgetme` because they are already coarsened.

## Product Rule

The farmer gets free, voice-first value immediately. Any later government or insurer-facing analytics must use server-side aggregated data only, with permission and without exposing farmer-identifying raw memory.
