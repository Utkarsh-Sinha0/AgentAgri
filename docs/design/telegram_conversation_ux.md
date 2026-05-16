# Telegram Conversation-Management UX — Design

**Status:** Draft (paper-first; no code yet)
**Owner:** AgentAgri agent team
**Date:** 2026-05-16
**Anchor commit:** `base-state-2026-05-16` (83ebf9f) — forward-only changes
**Related tasks:** #7 (this design), #8 (implementation, blocked on this doc)

---

## 1. Problem

Today every farmer message in Telegram is funneled through `_process_farmer_query`
(`app/bot/telegram_bot.py:735`) into one implicit conversation. Continuity is
real — `ConversationThread` rows persist with `running_summary`, `turn_count`,
and a `(farmer_id, field_id, crop_cycle_id, channel)` scope
(`app/models_memory.py:232-255`) — but the farmer has **no surface to see,
switch, close, or revisit threads**. Concretely:

- `get_or_create_thread` picks one active thread per scope and never lets the
  farmer start a new one or end the current one
  (`app/services/conversation.py:17-51`). `is_active` is read as a filter but
  **never flipped to `False`** anywhere in `app/` — so there is no
  "close thread" path.
- The active field and crop cycle are inferred with `.limit(1)`
  (`app/bot/telegram_bot.py:756-770`) regardless of what the farmer is asking
  about. A message about "धान" on a tomato-active field still gets routed to
  the tomato thread.
- `_process_farmer_query`'s keyboard (`app/bot/telegram_bot.py:823-844`) has no
  thread controls; the farmer cannot rename, close, switch, or list threads
  from the chat.
- Dashboard already renders per-field conversation summary widgets, but the bot
  side never references them, so the two surfaces drift.

The advisory quality work in tasks #3 / #4 / #5 leans on the thread being the
right one. Without UX, a wrong-scope thread will silently feed wrong
`running_summary` into the intent prompt and the contextualizer.

## 2. Goals / Non-goals

**Goals (this design):**
1. Give farmers an explicit, Hindi-first way to **see, switch, archive, and
   resume** conversation threads from Telegram.
2. Keep the existing `(farmer_id, field_id, crop_cycle_id, channel)` scope as
   the **canonical thread key**. No schema migration unless absolutely needed.
3. Make routing decisions **explicit and observable** (a single
   `route_to_thread()` helper) so future work — disease focus, multi-field
   farmers, multi-crop seasons — has one place to extend.
4. Keep behavior backwards-compatible: a farmer who never touches the new
   surface lands on exactly the same active thread they have today.

**Non-goals:**
- Multi-tenant or shared-thread features (extension agent + farmer).
- Web/SMS surfaces. Telegram only.
- Renaming the underlying primitives (`ConversationThread`,
  `ConversationTurn`) or splitting them into separate "sessions" / "threads".
- Cross-channel merge (telegram ↔ web ↔ ivr) — channel stays in the scope key.

## 3. Core primitives (today)

| Primitive | Code | Notes |
| --- | --- | --- |
| `ConversationThread` | `app/models_memory.py:232` | Has `is_active` (default True) and `title` (default "Field conversation" at `app/services/conversation.py:47`). |
| `ConversationTurn` | `app/models_memory.py:265` | Recorded by `record_turn` (`app/services/conversation.py:122`). |
| `get_or_create_thread` | `app/services/conversation.py:17` | Picks newest active thread; creates one if none. **Never reopens an archived thread.** |
| `latest_thread` | `app/services/conversation.py:54` | Used for context; ignores `is_active`. |
| `build_conversation_context` | `app/services/conversation.py:94` | Returns the running summary + last 4 turns; fed into intent and contextualizer. |
| Scope index | `ix_conversation_thread_scope` at `app/models_memory.py:328` | Already indexed; no extra index needed. |

The schema already supports everything UX needs. The gap is **surfacing and
control**, not data.

## 4. Telegram surface (chat-level)

We add three thin commands and three callback buttons. All Hindi-first with
English aliases.

### 4.1 Commands

| Command | Behavior |
| --- | --- |
| `/threads` | List the farmer's threads grouped by field/crop. One inline button per row to switch. Bottom row: "Show archived / पुराने दिखाएं". |
| `/newthread` | Archive the current active thread (in scope) and create a fresh one. Asks one confirm step: "इस बातचीत को बंद करें और नई शुरू करें? / Close this conversation and start fresh?". |
| `/endthread` | Archive the current active thread without creating a new one. The next farmer message will create one on demand (today's `get_or_create_thread` already does this). |

Aliases registered the same way as the existing commands at
`app/bot/telegram_bot.py:1882-1906` (`fields_command`, `usefield_command`,
etc.).

### 4.2 Keyboard additions

In `_process_farmer_query` (`app/bot/telegram_bot.py:823-844`) after the
existing rows, append one optional row when a thread is active:

```
[ 🗂 बातचीत / Threads ]   [ ✳ नई बातचीत / New thread ]
```

Both buttons use the existing inline callback router (`handle_callback` at
`app/bot/telegram_bot.py:281`). New `callback_data` values:

- `threads_list` → same handler as `/threads`
- `thread_new` → same handler as `/newthread`, but with the confirm step
  collapsed (the farmer already tapped the button intentionally).

### 4.3 Optional `/start` hint

When `/start` is run and the farmer has at least one active thread, append one
line: "🗂 आपकी पिछली बातचीत जारी है। `/threads` से देखें।" — non-intrusive,
no inline buttons. Avoids surprising returning farmers.

## 5. Dashboard surface (web)

Dashboard already has `_conversation_summary` per field. Add a tiny "threads"
strip to each field card:

- Lists threads for that field+crop scope (active first, archived collapsed).
- Tapping a thread expands to show `running_summary` and last 5 turns.
- No edit controls in v1 — chat is the primary control plane. Dashboard is
  read-only for threads in this iteration.

This keeps the dashboard from racing the bot for "who archives what."

## 6. Data-model touch points

**No migration.** Concretely:

- `is_active = False` is the archive signal. Currently `True` is implicit;
  flipping it on `/endthread` / `/newthread` is the only write change.
- `title` already exists (`app/models_memory.py:245`). Default
  "Field conversation" stays. For v1 we **do not** expose rename; the title is
  auto-derived (see §7) on first turn.
- `turn_count` and `updated_at` continue to be maintained by `record_turn`
  (`app/services/conversation.py:160-172`) — no change.

The only schema-adjacent question is whether to surface an archive timestamp
distinct from `updated_at`. Defer: `updated_at` + `is_active=False` is enough
for v1.

## 7. Routing logic

### 7.1 New helper: `route_to_thread()`

Insert a single helper in `app/services/conversation.py`, called from
`agent.py` between the existing context-load step and intent classification
(today's order, after the prior compaction-time refactor: load conversation
context → classify intent → ... around `app/services/agent.py:165-186`).

```python
async def route_to_thread(
    db: AsyncSession,
    *,
    farmer_id: str,
    field_id: str | None,
    crop_cycle_id: str | None,
    channel: str = "telegram",
    message: str,
    intent_crop_name: str | None,
    intent_state_or_region: str | None,
) -> ConversationThread:
    """
    Decide which thread this turn belongs to.

    Order:
      1. Active thread for the exact scope → use it.
      2. If intent_crop_name is set and differs from the active scope's crop,
         look for any *active* thread under farmer_id with matching crop_name
         → use it. (Cross-field follow-up on a different crop.)
      3. Fall back to get_or_create_thread (today's behavior).
    """
```

### 7.2 Pseudocode

```
active = await get_active_thread_for_scope(...)
if active:
    return active

if intent_crop_name:
    cross = await find_active_thread_by_crop(farmer_id, intent_crop_name)
    if cross:
        return cross

return await get_or_create_thread(...)
```

### 7.3 Why a helper, not in-line

- One observable place to log routing decisions.
- Future "disease-focused" threads or "season-scoped" threads slot in here
  without touching `agent.py`.
- Test coverage is straightforward: cases are crop/scope tuples, not the full
  agent pipeline.

### 7.4 Title derivation (first turn)

When `turn_count` transitions 0 → 1 in `record_turn`, derive title:

```
title = f"{crop_name or 'field'} • {topic_tag or 'general'}"
```

This is a tiny addition to `record_turn` (`app/services/conversation.py:160`)
that runs only on first persist. Kept inside the existing function to avoid a
second write.

## 8. Memory integration

- `build_conversation_context` already feeds the intent prompt (task #4 wired
  this in). Switching threads simply changes which `running_summary` and which
  turns are returned — the existing function needs no edit.
- The new routing helper runs **before** intent classification, so the prompt
  context is for the right thread on the first try.
- `previous_evidence_article_ids` (`app/services/conversation.py:69`) follows
  the same `latest_thread` lookup; it will resolve to whichever thread routing
  picked, which is the intended behavior.

## 9. Failure modes

| Failure | Behavior |
| --- | --- |
| Farmer taps "New thread" mid-question | Confirm step prevents accidental discard. After confirm, the question is **not** carried over; farmer types it again. (Carrying over implicitly is harder to test and easy to do wrong; deferred.) |
| Active thread for scope is archived externally (e.g., dashboard bug) | `get_or_create_thread` will create a fresh one. No crash. |
| `intent_crop_name` is wrong (LLM hallucination) | Cross-scope routing is gated by "matching crop" + "active thread" — wrong crop simply means no match, fallback to scope thread. |
| `/threads` for a brand-new farmer | "अभी कोई बातचीत नहीं है। कोई सवाल पूछें / No conversations yet. Ask a question." |
| Race: two messages in flight, second one archives mid-process | `is_active` check is per-DB-row; second arrival re-resolves and creates new thread. Outcome is correct, occasional duplicate thread. Acceptable. |

## 10. Implementation phasing

1. **Helper + tests.** Add `route_to_thread()` with unit tests over the three
   branches. No bot wiring yet. *(Self-contained, revertible.)*
2. **Wire helper into agent.** Replace today's implicit `get_or_create_thread`
   call in the agent pipeline with `route_to_thread`. Live-probe with the
   `evals/probe_intent.py` queries that already cover follow-up. *(One-line
   call site swap; trivially revertible.)*
3. **Commands.** `/threads`, `/newthread`, `/endthread` + callback handlers.
   *(New code paths; existing flows untouched.)*
4. **Keyboard row.** Add the two buttons to `_process_farmer_query`.
   *(Single-line addition.)*
5. **`/start` hint.** Conditional one-liner.
6. **Dashboard read-only strip.** Last; no chat dependency.

Each phase is a separate commit so we keep the reversibility rule
(`feedback_reversibility`).

## 11. Resolved design decisions (2026-05-16)

1. **`/newthread` archive step** → **Two-step confirm.** Show
   "इस बातचीत को बंद करें और नई शुरू करें? / Close this conversation and
   start fresh?" before archiving. Safer for misclicks on small screens.
2. **Rename in v1** → **No.** Title is auto-derived from crop + topic on
   first turn (§7.4). `/renamethread` deferred to a future iteration.
3. **`/start` hint** → **One-line text hint.** Append
   "🗂 आपकी पिछली बातचीत जारी है। `/threads` से देखें।" when an active
   thread exists. No inline buttons in `/start`.
4. **Cross-scope routing** → **Only if current scope has no active
   thread.** Matches the §7.2 draft: cross-scope crop match wins only as a
   fallback. Keeps behavior predictable when LLM crop extraction is wrong.
5. **Auto-archive visibility in `/threads`** → **Hidden by default.** Show
   only active threads in the main list; expose archived ones under a
   "पुराने दिखाएं / Show archived" inline button.

These decisions are now binding for phase 1 of task #8.

---

## Appendix A — File-touch checklist (for phase 1)

- `app/services/conversation.py`: add `route_to_thread()`; small edit in
  `record_turn` for title derivation on first turn.
- `app/services/agent.py`: replace the implicit thread acquisition with the
  helper call.
- `app/bot/telegram_bot.py`: register `threads_command`, `newthread_command`,
  `endthread_command` (mirror `fields_command` at line 1420 / `usefield_command`
  at 1449); extend `handle_callback` (line 281) with `threads_list` and
  `thread_new`; extend `_process_farmer_query` keyboard (line 823-844).
- `tests/test_conversation_routing.py`: new file. Three unit tests covering
  the three `route_to_thread` branches; one e2e covering archive-then-new.
- `tests/test_conversation_impact.py`: unchanged. Existing assertions
  (`test_conversation_turns_preserve_farmer_context` at
  `tests/test_conversation_impact.py:15`) still pass because thread creation
  flow is preserved.
