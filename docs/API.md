# AgentAgri API

Protected `/api/*` routes require `X-AgriMesh-API-Key` when `APP_ENV=production` or `AGRIMESH_REQUIRE_API_KEY=true`.

Error shape:

```json
{
  "detail": "Invalid API key",
  "error": {
    "code": "http_error",
    "message": "Invalid API key",
    "status_code": 401
  }
}
```

## Health

### `GET /health`

Response:

```json
{"status":"healthy","version":"4.0.0","model":"gemma4:e4b","grammar_decoding":true,"environment":"development"}
```

### `GET /api/health/degradation`

Response:

```json
{"level":5,"level_name":"FULL","ollama_healthy":true,"db_healthy":true,"latency_ms":12}
```

## Farmer

### `GET /api/farmer-dashboard?farmer_id={id}`

Response includes `farmer`, `fields`, `weather`, `mandi`, `advisories`, `finance`, `memory`, `impacts`, and `clusters`.

### `PUT /api/farmers/{farmer_id}/profile`

Request:

```json
{"farm_size_acres":2.5,"irrigation_source":"canal","risk_tolerance":"medium"}
```

Response:

```json
{"profile":{"farmer_id":"...","profile_completeness":0.42}}
```

### `GET /api/farmers/{farmer_id}/advisories?limit=20`

Response:

```json
{"count":1,"advisories":[{"id":"...","risk_level":"WATCH","confidence":"MEDIUM","actions_text":[]}]}
```

### `GET /api/farmers/{farmer_id}/conversation`

Response:

```json
{"thread":{"id":"...","turn_count":3},"turns":[{"user_message":"...","agent_response":"..."}]}
```

## Advisory Impact

### `GET /api/advisories/{advisory_id}/impact-network`

Builds missing impact nodes if needed.

### `GET /api/impact-network?limit=20`

Response:

```json
{"count":1,"impacts":[{"action_text":"Improve field drainage","impact_level":"medium"}]}
```

## Cluster Review

### `GET /api/clusters?district=Munger&tehsil=Munger%20Sadar`

Response:

```json
{"count":1,"clusters":[{"id":"...","crop_name":"rice","severity":0.72,"status":"pending"}]}
```

### `GET /api/clusters/{cluster_id}`

Returns full cluster details, observations, and advisories.

### `POST /api/clusters/{cluster_id}/review`

Request:

```json
{"action":"approve_broadcast","extension_worker_id":"worker-1","broadcast_message":"Check rice leaves today."}
```

Response:

```json
{"status":"broadcast","cluster_id":"..."}
```

## Model and Gemma Showcase

### `GET /api/models`

Response:

```json
{
  "current": "gemma4:e4b",
  "fallback": "gemma4:e2b",
  "options": [{"id":"gemma4:e4b","label":"gemma4 e4b","role":"primary"}],
  "grammar_decoding": true,
  "timeout_seconds": 90.0
}
```

### `GET /api/ai/showcase?farmer_id={id}&limit=5`

Response:

```json
{
  "models": {"current":"gemma4:e4b","fallback":"gemma4:e2b","options":["gemma4:e4b","gemma4:e2b"]},
  "latest_advisory": {"id":"...","confidence":"MEDIUM","latency_ms":400},
  "vision": {"image_path":null,"analysis":null,"confidence":null},
  "reasoning_trace": [{"step":"Evidence retrieval","summary":"3 evidence articles selected via fast retrieval."}],
  "tool_calls": [{"tool_name":"get_forecast","status":"used"}],
  "citations": [{"source_name":"AgriMesh Graph-Wiki","trust_level":"medium"}],
  "history": []
}
```

## Weather and Market

### `GET /api/weather/forecast?field_id={id}&days=5`

Response:

```json
{"forecast":[{"date":"2026-05-15","rainfall_mm":2.1}],"history":[]}
```

### `GET /api/market-prices?crop=rice&district=Munger&days=7`

Response:

```json
{"crop":"rice","district":"Munger","prices":{"prices":[{"type":"paddy_common","history":[]}]},"msp":{"msp_per_quintal":2300}}
```

## Registry, Memory, Eval

### `GET /api/stats`

Returns aggregate counts for farmers, advisories, sources, conversations, memory atoms, and impact nodes.

### `GET /api/memory/summaries?scale=field&limit=20`

Returns living-memory summaries.

### `GET /api/sources`

Returns registered evidence sources and freshness metadata.

### `GET /api/eval/latest`

Returns latest evaluation results, or `{"status":"no_eval_yet"}`.

### `GET /api/eval/history`

Returns the ten latest eval result summaries.

