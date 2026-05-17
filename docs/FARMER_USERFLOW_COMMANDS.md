# AgentAgri Farmer Userflow Commands

Date verified: 2026-05-18

This is the farmer-facing Telegram command/action matrix for `@agrimitrbot`.
Use it for live smoke testing and for asking a farmer to exercise every major
flow. Voice, photo, and normal text are first-class actions; commands are
fallback controls.

## 1. Start And Onboard

| Action | Farmer sends/taps | Expected result |
| --- | --- | --- |
| Open bot | `/start` | Bilingual welcome, demo/register/help buttons. Existing farmers see continue/new-thread/dashboard options. |
| See all commands | `/help` | Hindi-first command menu grouped by getting started, daily work, crop cycle, privacy, and trust. |
| Demo farm | `/demo` or "Demo: sample farm + memory" | Seeds a sample farmer, field, crop cycle, memory, dashboard data. |
| Text registration | `/register` | Step-by-step name, village, district, tehsil, crop, soil/stage prompts. |
| Voice registration | send one voice note with name, village, district, crop | STT extracts registration fields, creates farmer/field/crop records, replies by text and voice when voice pipeline is available. |
| Edit profile | `/edit` | Shows editable farmer profile fields with inline choices. |
| Profile details | `/profile` | Shows/collects extended farm profile used for advice personalization. |

## 2. Ask For Advice

| Action | Farmer sends/taps | Expected result |
| --- | --- | --- |
| Text question | Any non-command message, for example `धान में पत्ती भूरे धब्बे हैं` | Agent classifies intent, retrieves evidence, verifies advice, stores observation/advisory/turn. |
| Voice question | Voice or audio note | Bot transcribes, persists transcript, runs agent, replies in detected language; TTS is attempted when enabled. |
| Photo diagnosis | Send crop photo, optionally with caption | Bot downloads image, creates photo observation, runs vision + agent flow, asks for missing context if needed. |
| Photo then voice | Send photo, then send related voice note | Bot pairs pending photo with the spoken question for multimodal diagnosis. |
| Evidence button | `Evidence` inline button after advice | Shows advisory evidence cards/sources where available. |
| Verifier button | `Verified` / `Safe Advice` inline button after advice | Shows verifier status for the latest advice. |

## 3. Daily Farm Work

| Command/action | Expected result |
| --- | --- |
| `/prices` | Mandi prices and MSP for the active crop, citing seeded AgMarknet/MSP data. |
| `/tasks` | Current crop-calendar tasks for the active cycle. |
| `/calendar` | Upcoming crop-stage/task view. |
| `/expense <amount> <category> <note>` | Logs crop-cycle expense. |
| `/sale <amount> <quantity> <note>` | Logs sale/revenue. |
| `/finance` | Shows finance rollup/P&L for the active cycle. |
| `/memory` | Shows what the agent remembers about the farm. |
| `/dashboard` | Sends a private dashboard/PWA link or local-dashboard callback. |

## 4. Field And Crop Switching

| Command/action | Expected result |
| --- | --- |
| `/field` | Starts field registration flow for an existing farmer. |
| `/fields` | Lists the farmer's fields with switch buttons. |
| `/usefield <number>` | Sets active field in session. |
| `/crop` | Starts crop-cycle registration for the active field. |
| `/crops` | Lists active crop cycles. |
| `/usecrop <number>` | Sets active crop cycle in session. |
| `/newcycle <crop_name> <variety> <stage>` | Closes previous active cycle on field if needed and starts a new cycle. |
| `/closecycle` | Marks current cycle inactive and suggests next crop rotation. |

## 5. Conversation Threads

| Command/action | Expected result |
| --- | --- |
| `/threads` or "Threads" button | Lists active conversation threads; archived threads are hidden by default. |
| `/newthread` or "New thread" button | Confirms archive of the current thread and starts fresh after farmer confirms. |
| `/endthread` | Archives the current active thread; next question creates a new one. |
| Tap a thread row | Switches current session to that conversation thread. |

## 6. Trust, Feedback, Outcomes

| Command/action | Expected result |
| --- | --- |
| `/why` | Explains why the last recommendation was given. |
| `/sources` | Shows source freshness and evidence source status. |
| `/feedback 1-5 <comment>` | Stores farmer rating/comment on the last advisory. |
| `/outcome <improved\|no_change\|worsened\|not_tried> <comment>` | Updates the linked observation outcome, writes memory, and can trigger outbreak checks for negative disease/pest outcomes. |
| Kisan Call Centre escalation | In severe, uncertain, fast-spreading, or chemical-use cases | Bot should ask for more field evidence and advise Kisan Call Centre `1800-180-1551` or local KVK/agriculture officer. |

## 7. Privacy And Voice Settings

| Command/action | Expected result |
| --- | --- |
| `/mydata` | Shows recent non-redacted farmer-owned memory atoms only. |
| `/forgetme` | Shows confirmation before soft-redacting raw memory. |
| Confirm forget | Redacts raw memory atoms; aggregate anonymous summaries may remain. |
| Cancel forget | Leaves memory untouched. |
| `/voice_lang <code>` | Sets preferred voice language, for example `hi-IN`, `en-IN`, `bho`. |
| `/voice_reply text` | Text reply only. |
| `/voice_reply both` | Text plus voice reply when available. |
| `/voice_reply voice` | Voice-first reply when available. |
| `/health` | Shows bot/service readiness summary. |

## 8. Live Test Order

1. `/start`
2. Send one Hindi voice registration note with name, village, district, crop.
3. `/mydata`
4. Ask a rice or wheat issue by text.
5. Ask the same issue by voice.
6. Send a crop photo without caption, then send a voice follow-up.
7. `/prices`
8. `/tasks`
9. `/expense 500 seed demo`
10. `/sale 2000 1quintal demo`
11. `/finance`
12. `/threads`
13. `/newthread`, confirm, then ask a new question.
14. `/feedback 4 helpful`
15. `/outcome improved symptoms reduced`
16. `/sources`
17. `/why`
18. `/forgetme`, cancel first; only confirm in a disposable test account.

Do not use a real farmer account for destructive privacy testing unless the
farmer explicitly wants their raw memory redacted.
