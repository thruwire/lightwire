# LightWire

LightWire is a lightweight control plane for multi-agent systems.

It coordinates workflows across agents, tools, connectors, and scheduled jobs while delegating reasoning, tool execution, and iteration loops to managed agent platforms such as Claude Managed Agents. LightWire decides which agent runs, what context it receives, and where its output goes next. The heavy execution stays in provider infrastructure; LightWire remains a small coordination service.

LightWire lets you build multi-agent systems where the intelligence runs in the cloud, and only the coordination runs locally.

LightWire is part of the ThruWire ecosystem. Learn more at [thruwire.ai](https://thruwire.ai).

## Why It Exists

Managed agents are good at executing work. They are not, by themselves, a complete coordination layer for larger systems.

LightWire exists to make multi-agent workflows explicit:

- routes decide which agent handles which event
- prompt templates define task-specific inputs
- captured outputs become structured upstream context for the next step
- connectors and heartbeats feed the same routing pipeline

Instead of relying on shared memory, ad hoc filesystem conventions, or emergent behavior inside a long-running loop, LightWire gives you a declared control plane for how agents work together.

## What You Can Build

- Multi-agent research -> analysis -> output pipelines
- Slack-native AI bots that route requests across multiple managed agents
- Scheduled workflows driven by heartbeat events
- Tool-augmented agents using built-in tools and remote MCP servers
- OpenClaw-style automation with explicit routing instead of implicit memory coordination

## Why LightWire

- Declarative orchestration: routes describe system behavior directly
- Direct agent-to-agent routing: outputs are captured and forwarded explicitly
- No shared memory or filesystem contract for normal handoffs
- Cloud-native execution: managed agents perform the heavy work
- Small control-plane footprint: the coordination service can run on a small VM
- Built-in connectors: Slack, API, Telegram, and scheduled heartbeats enter the same runtime
- Provider-neutral architecture: the routing model is not hardcoded to one backend
- Versionable workspace definition: agents, routes, prompts, tools, and connectors live in files

## Separation Of Concerns

LightWire is deliberately split across two layers:

- Managed agents are the execution layer.
  They handle reasoning, tool use, and iterative task execution.
- LightWire is the coordination layer.
  It decides which agent runs, how messages are routed, and what happens after each result.

That separation is the core design choice in this repo. LightWire does not try to host the agent loop. It coordinates cloud-executed agents into structured workflows.

## LightWire vs OpenClaw-style Agents

| | OpenClaw-style | LightWire |
|--|--|--|
| Core model | Agent loop | Control plane |
| Execution | Local or hosted loop | Managed agents (cloud) |
| Coordination | Memory / filesystem | Explicit routing |
| System shape | Emergent | Declared |
| Infra | Full runtime environment | Small control plane |

OpenClaw executes behavior inside a loop. LightWire defines how multiple agents work together.

## How It Works

The runtime shape is simple:

`event -> route -> agent -> captured output -> next route -> final action`

Inbound events from API, Slack, Telegram, heartbeats, or prior agent outputs are normalized into one message shape. Routes match those messages, render prompt templates, run the target managed agent, capture the result, and optionally feed that result into the next route.

See [Architecture](./docs/architecture.md) for the full runtime model.

## Workspace Model

LightWire loads a versionable workspace from `WORKSPACE_PATH`. That workspace defines:

- agents
- prompt templates
- skills
- tools and MCP servers
- routes
- heartbeat schedules
- connector config

See [Workspace](./docs/workspace.md) for the full file layout and config model.

## Getting Started

The fastest way to start a working local instance is with Docker Compose:

```bash
cp .env.example .env
docker compose run --rm lightwire python scripts/deploy_managed_agents.py
docker compose up --build
```

To make that work, you need:

- a `.env` file copied from `.env.example`
- `ANTHROPIC_API_KEY` for live managed-agent execution, or `LIGHTWIRE_FAKE_CLAUDE=true` for local demos and tests
- the workspace files under `./workspace`, or `WORKSPACE_PATH` pointing to a different workspace
- connector tokens such as `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, or `TELEGRAM_BOT_TOKEN` only if you want those connectors enabled
- any MCP secret env vars referenced by `workspace/tools.yaml`

You can also run LightWire locally without Docker:

1. Install dependencies with `pip install -e .[dev]`.
2. Install the Anthropic `ant` CLI if you want live managed-agent deploys.
3. Run `python scripts/deploy_managed_agents.py`.
4. Start the API with `uvicorn app.main:app --reload`.

For hosting, the common shape is a small long-running control-plane service with persistent storage for SQLite and access to the internet for provider and connector APIs. It can run on a laptop for development, a small VM or VPS for simple deployments, or a container platform or Kubernetes cluster if you already operate one.

## Quickstart

1. Copy `.env.example` to `.env`.
2. Install dependencies with `pip install -e .[dev]`.
3. Install the Anthropic `ant` CLI if you want live managed-agent deploys.
4. Set `ANTHROPIC_API_KEY` if you want live managed-agent execution.
5. Run `python scripts/deploy_managed_agents.py`.
6. Start the API with `uvicorn app.main:app --reload`.

For containerized startup:

```bash
docker compose run --rm lightwire python scripts/deploy_managed_agents.py
docker compose up --build
```

Operational details live in [docs/operations.md](./docs/operations.md).

## Documentation

- [Docs Index](./docs/README.md)
- [Architecture](./docs/architecture.md)
- [Workspace](./docs/workspace.md)
- [Config Formats](./docs/config-formats.md)
- [Connectors](./docs/connectors.md)
- [Provider Integration](./docs/provider-integration.md)
- [Operations](./docs/operations.md)
- [Development](./docs/development.md)

## API Surface

- `GET /healthz`
- `POST /events`
- `POST /heartbeats/{heartbeat_id}/run`
- `GET /messages/{id}`
- `GET /sessions/{id}`
- `GET /routes`
- `GET /heartbeats`
- `POST /admin/reload-config`

## Demo

Run the included demo:

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
