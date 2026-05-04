# Operations

This page covers setup, deploy/apply behavior, runtime startup, Docker, and troubleshooting. The root [README.md](../README.md) keeps only the minimal quickstart.

## Local Setup

1. Copy `.env.example` to `.env`.
2. Set `ANTHROPIC_API_KEY` if you want live provider calls.
3. Install the Anthropic `ant` CLI and ensure it is on `PATH`, or set `ANT_BIN`.
4. Set connector tokens such as `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, or `TELEGRAM_BOT_TOKEN` if you want those connectors enabled.
5. Set any MCP secret env vars referenced by `workspace/tools.yaml`.
6. Install dependencies with `pip install -e .[dev]`.
7. Run `python scripts/deploy_managed_agents.py`.
8. Start the API with `uvicorn app.main:app --reload`.

Optional settings commonly used in local or deployment repos:

- `WORKSPACE_PATH` if the workspace is not `./workspace`
- `LIGHTWIRE_WORKSPACE_ID` to namespace provider-managed resources per deployment
- `LIGHTWIRE_FAKE_CLAUDE=true` for tests or demos without live provider calls
- `LIGHTWIRE_DELETE_COMPLETED_SESSIONS=false` if you intentionally want remote sessions to remain available after completion
- `ANTHROPIC_BASE_URL` if your provider environment exposes the managed-agent APIs at a different base URL

## Docker

Run:

```bash
docker compose up --build
```

The compose setup:

- mounts `./workspace` into `/app/workspace` as read-only
- persists SQLite under `/app/data`
- sets `WORKSPACE_PATH=/app/workspace`
- includes the Anthropic `ant` CLI inside the image so containerized provisioning does not depend on a host installation

## Deploy Behavior

Deployment is explicit. Run the deploy entrypoint when you want LightWire to create, update, or repair provider resources:

```bash
python scripts/deploy_managed_agents.py
```

Deploy/apply:

1. loads the workspace from `WORKSPACE_PATH`
2. verifies cached provider IDs remotely
3. recreates missing environments, memory stores, vaults, or credentials when needed
4. updates agent definitions
5. persists repaired or newly created provider IDs into SQLite

## Startup Behavior

On application startup LightWire:

1. loads the workspace from `WORKSPACE_PATH`
2. initializes SQLite tables
3. validates that previously deployed provider IDs exist in SQLite
4. attaches those cached IDs to runtime config
5. starts the heartbeat scheduler
6. starts enabled connectors

On shutdown LightWire stops background loops cleanly.

When a managed-agent run finishes successfully, LightWire captures the final output, records the remote session ID in SQLite for traceability, and then deletes the remote Anthropic session by default so the provider console does not fill up with idle leftovers.

## State And Persistence

LightWire persists local orchestration state in SQLite only.

That state includes:

- normalized messages
- session records
- connector cursors
- provider resource IDs
- heartbeat timing

The normal downstream handoff is still explicit routed output, not shared state between agents.

## Demo

Run:

```bash
python scripts/run_demo.py
```

Or post an API event:

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
- rerun `python scripts/deploy_managed_agents.py` if startup says provider resources or agents are missing
- use `LIGHTWIRE_FAKE_CLAUDE=true` only when you intentionally want mock provider behavior
- install the Anthropic CLI or set `ANT_BIN` if provisioning fails because `ant` is missing
