# AgriMesh 2-Minute Demo Script

## Goal

Tell a story first, then prove the engineering. The video should make judges feel the urgency of smallholder decision-making, then show that AgriMesh is not a concept video: it is a working Gemma 4 agent with onboarding, multimodal diagnosis, retrieval, tools, memory, safety checks, and a dashboard observability layer.

## Recommended Track Framing

Impact Track: Global Resilience  
Special Technology Alignment: Ollama / local Gemma 4 inference

## Story Before The Demo

A farmer does not wake up with one neat AI task. They wake up with a field, a weather risk, a pest they cannot identify, a market price they cannot trust, and yesterday's decision still affecting today's crop.

For smallholder farmers, advice must be local, fast, affordable, and safe. A generic chatbot can sound confident, but agriculture needs something better: an evidence-based agent that sees the crop, understands the farmer's field, checks live tools, remembers outcomes, cites sources, and knows when to say "not enough data."

AgriMesh is that agent. It turns Telegram into a farmer interface and a web dashboard into an engineering control plane. Powered by Gemma 4, it combines multimodal crop diagnosis, tool calling, retrieval, memory, safety verification, and local-first inference into one agricultural workflow. The more farmers use it, the smarter its local memory becomes.

## Two-Minute Audio Transcript

**00:00 - 00:12**  
Every farming decision is connected. A pest on a rice leaf is not just a pest question. It depends on the crop stage, the weather, the farmer's location, previous advice, local outbreak signals, and market pressure.

**00:12 - 00:24**  
Smallholder farmers need more than a chatbot. They need an evidence-based agricultural agent that can see, reason, verify, remember, and safely say when the data is missing.

**00:24 - 00:34**  
This is AgriMesh, built with Gemma 4 for the Kaggle Gemma 4 Good Hackathon. The farmer starts in Telegram, chooses a language, and begins a guided registration flow.

**00:34 - 00:49**  
AgriMesh captures the farmer's name, phone, district, pincode, block, village, field area, soil type, crop, and crop stage. This is not just onboarding. This creates the context graph for every future advisory.

**00:49 - 01:06**  
Now the farmer sends a crop photo and says: "I have got white insects on my rice saplings." Gemma 4 handles the visual signal, the agent retrieves local crop evidence, checks location-aware outbreak context, and routes the question through safety verification.

**01:06 - 01:18**  
The response is structured and cautious: a local outbreak warning, a pest hypothesis such as whiteflies or mealybugs, preventive action, confidence language, and a memory trail for follow-up.

**01:18 - 01:31**  
The same session handles a Hindi weather query. AgriMesh uses the farmer's stored Patna location, calls the weather workflow, and returns a five-day forecast in a farmer-readable format.

**01:31 - 01:41**  
When the farmer asks for MSP data and the system does not have enough evidence, it does not hallucinate. It degrades safely and says the data is not available.

**01:41 - 01:53**  
On the dashboard, the same conversation becomes structured telemetry: crop analysis, mandi prices, weather cards, model traces, tool calls, citations, safety checks, and advisory history.

**01:53 - 02:00**  
AgriMesh is a practical agricultural intelligence layer: Gemma 4 reasons, tools ground the facts, memory improves the next answer, and verification protects the farmer.

## Parallel Video Direction And Engineering Logic

| Time | Visual Direction | Audio / Story Beat | Engineering Logic To Show |
|---|---|---|---|
| 00:00 - 00:06 | Start with Telegram blank chat. Keep the framing calm and close. | "Every farming decision is connected." | Establish the farmer-first surface. Telegram is the low-friction interface. |
| 00:06 - 00:12 | Show `/start` and the language picker. | "It depends on crop stage, weather, location, previous advice..." | Localization starts before inference. Language choice configures the user experience. |
| 00:12 - 00:24 | Show welcome message and bottom menu. | "Smallholder farmers need more than a chatbot." | Position AgriMesh as an agent: diagnosis, prices, memory, dashboard, and voice/photo entry. |
| 00:24 - 00:34 | User sends `/demo`; bot lists Gemma capabilities. | "This is AgriMesh, built with Gemma 4..." | Demonstrate capability disclosure and entry into a state-machine workflow. |
| 00:34 - 00:49 | Fast cuts through Utkarsh, phone, Patna, pincode, Patna Sadar, Barh, Field 1, 1.24 acres, Loam, Rice, Vegetative. | "This creates the context graph for every future advisory." | Identity, geospatial anchor, field asset, soil, crop, and crop stage become structured context. |
| 00:49 - 00:58 | Open gallery, select rice pest image, add caption. | "Now the farmer sends a crop photo..." | Multimodal input: image plus natural language prompt. |
| 00:58 - 01:06 | Bot processes; show caution and preventive action. | "Gemma 4 handles the visual signal..." | Vision hypothesis, RAG evidence, local outbreak context, tool routing, verifier. |
| 01:06 - 01:18 | Highlight caution box, pest names, action recommendation, confidence. | "The response is structured and cautious..." | Structured template output: warning, diagnosis, action, confidence, persistence. |
| 01:18 - 01:31 | Show Hindi query and weather forecast response. | "The same session handles a Hindi weather query." | Cross-lingual intent detection and stored location reuse. |
| 01:31 - 01:41 | Show MSP question and "not enough data available." | "It does not hallucinate." | Boundary handling: missing data produces safe degradation. |
| 01:41 - 01:46 | Switch to desktop dashboard overview. | "The same conversation becomes structured telemetry." | State sync from chat into dashboard payload. |
| 01:46 - 01:49 | Crop Analysis graph. | "Crop analysis..." | Crop lifecycle and growth/NDVI-style visualization. |
| 01:49 - 01:51 | Mandi Prices table. | "Mandi prices..." | Economic tool output becomes tabular data. |
| 01:51 - 01:53 | Weather cards. | "Weather cards..." | Forecast data is converted from chat response to dashboard visualization. |
| 01:53 - 01:57 | AI Agent and History tabs. | "Model traces, tool calls, citations, safety checks..." | Observability layer: reasoning trace, verifier reports, citations, raw parsed input. |
| 01:57 - 02:00 | Settings tab or final dashboard overview. | "Gemma 4 reasons, tools ground, memory improves, verification protects." | Close on architecture proof and deployment readiness. |

## Engineering Voiceover Notes

Use these phrases to sound technical without becoming too dense:

- "This registration is not form filling; it is state initialization."
- "The pincode, village, field, soil, crop, and stage become the context window for future reasoning."
- "Gemma 4 is used as the reasoning layer, not as an unchecked free-text authority."
- "Tool calls ground live facts such as weather, mandi, schemes, and finance."
- "The verifier sits between model output and farmer action."
- "When evidence is missing, AgriMesh degrades safely instead of hallucinating."
- "The dashboard proves the demo is backed by real telemetry: traces, citations, safety checks, and model configuration."

## Optional High-Impact Closing Line

AgriMesh turns every field interaction into safer local intelligence: one farmer gets help now, and the community gets a better warning system over time.

## Shorter 90-Second Backup Transcript

Every farming decision is connected. A pest on a rice leaf depends on crop stage, local weather, previous actions, market pressure, and whether nearby farmers are seeing the same threat.

AgriMesh is an evidence-based agricultural agent built with Gemma 4. The farmer starts in Telegram, chooses a language, and registers their location, field, soil, crop, and stage. That onboarding becomes the context graph for future advice.

When the farmer uploads a rice photo with white insects, Gemma 4 analyzes the image, the agent retrieves crop evidence, checks local outbreak context, calls relevant tools, and passes the answer through a verifier. The reply includes a warning, pest hypothesis, preventive action, confidence, and citations.

The same session handles a Hindi weather query using the farmer's stored Patna location. And when MSP data is not strong enough, AgriMesh says so instead of inventing an answer.

On the dashboard, the conversation becomes structured telemetry: crop analysis, mandi prices, weather cards, model traces, citations, safety checks, and advisory history.

AgriMesh is not just a chatbot. Gemma 4 reasons, tools ground the facts, memory improves the next answer, and verification protects the farmer.
