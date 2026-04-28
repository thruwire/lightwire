# Operations

## Local Development

1. Copy `.env.example` to `.env`.
2. Set `ANTHROPIC_API_KEY` if you want live provider calls.
3. Set connector tokens such as `SLACK_BOT_TOKEN` or `TELEGRAM_BOT_TOKEN` if you want polling enabled.
4. Set any MCP secret env vars referenced by `workspace/tools.yaml`.
5. Install dependencies with `pip install -e .[dev]`.
6. Start the API with `uvicorn app.main:app --reload`.

## Docker

Run:

```bash
docker compose up --build
```

The compose setup:

- mounts `./workspace` into `/app/workspace` as read-only
- persists SQLite under `/app/data`
- sets `WORKSPACE_PATH=/app/workspace`

## Startup Behavior

On application startup ThruFlow:

1. loads the workspace from `WORKSPACE_PATH`
2. initializes SQLite tables
3. ensures provider environment and memory store IDs exist
4. ensures MCP vaults and credentials exist for enabled agents
5. starts the heartbeat scheduler
6. starts enabled polling connectors

On shutdown ThruFlow stops background loops cleanly.

## State Files

ThruFlow persists local orchestration state in SQLite only.

That state includes:

- normalized messages
- session records
- connector cursors
- provider resource IDs
- heartbeat timing

Shared durable task artifacts should live in the mounted provider memory store, not in the git repository.

Agents can return natural language text. ThruFlow extracts `/mnt/memory/...` paths from that text and uses those paths as the normal downstream handoff input.

## Demo

You can exercise the demo pipeline with:

```bash
python scripts/run_demo.py
```

Or by posting an API event:

```bash
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{
    "source": "api",
    "type": "message.created",
    "payload": {
      "text": "What are the tradeoffs of using shared memory stores for agent handoffs?"
    }
  }'
```

## Troubleshooting

Common checks:

- `GET /healthz` returns `{"status":"ok"}`
- inspect `workspace/routes.yaml` when messages are not creating sessions
- inspect `connector_cursors` when a connector appears stuck
- inspect `provider_state` when provider resources are unexpectedly recreated
- run with `THRUFLOW_FAKE_CLAUDE=true` to isolate orchestration from live provider behavior
