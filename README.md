# ThruFlow

ThruFlow is an orchestrator of agent harnesses, currently supporting Claude Managed Agent harnesses while remaining extensible to other managed-agent harnesses over time.

Unlike local multi-agent setups that require your own hardware, ThruFlow relies on Claude Managed Agents so the heavy execution runs in Anthropic’s infrastructure.

## Documentation

- [Docs Index](./docs/README.md)
- [Architecture](./docs/architecture.md)
- [Workspace](./docs/workspace.md)
- [Config Formats](./docs/config-formats.md)
- [Connectors](./docs/connectors.md)
- [Provider Integration](./docs/provider-integration.md)
- [Operations](./docs/operations.md)
- [Development](./docs/development.md)

## Workspace Structure

ThruFlow loads its provider-neutral workspace from `WORKSPACE_PATH` and expects this layout:

```text
workspace/
  agents/
    <agent_id>/
      config.yaml
      AGENT.md
  skills/
    <skill_id>/
      config.yaml
      SKILL.md
  prompt_templates/
    <template_id>.md
  tools.yaml
  routes.yaml
  heartbeats.yaml
  slack.yaml
  telegram.yaml
```

- `workspace/agents/*/AGENT.md` holds stable provider-neutral agent instructions.
- `workspace/skills/*/SKILL.md` holds reusable skill instructions.
- `workspace/prompt_templates/*.md` holds route-specific task prompts rendered with Jinja-style variables.
- `workspace/tools.yaml` is the shared tool registry for built-in tools and remote MCP servers.
- `workspace/routes.yaml` wires messages to agents and prompt templates.
- `workspace/heartbeats.yaml` defines scheduled routable prompts.
- `workspace/slack.yaml` defines Slack polling behavior.
- `workspace/telegram.yaml` defines Telegram polling, chat allowlists, and optional final replies.

Memory is provider-managed, mounted into sessions at runtime, and not stored in git.

For a deeper breakdown of workspace files and responsibilities, see [docs/workspace.md](./docs/workspace.md).

## Control Plane Vs Data Plane

ThruFlow uses messages and routes as the control plane.

Shared memory is the data plane. Agents write durable outputs to `/mnt/memory` and mention the paths in their final response. ThruFlow extracts those paths and passes them to downstream agents.

The orchestrator does not need to parse artifact contents in v1. Downstream agents read memory artifacts themselves.

## Tools And MCP

ThruFlow keeps environments internal and places tool configuration at the workspace level.

- Workspace-level tool config declares built-in tool defaults and remote MCP servers.
- Agent configs activate only the built-in tools and MCP tools they need.
- Claude Managed Agents requires MCP servers to be remote HTTP endpoints; local stdio MCP servers are not sufficient.

For MCP auth, ThruFlow reads secret references from `workspace/tools.yaml`, creates or reuses Anthropic vaults and credentials, stores the resulting IDs in SQLite, and attaches the relevant `vault_ids` when sessions start. This keeps secrets out of reusable agent definitions while still supporting generic third-party MCP servers.

Supported auth patterns in this repo today:

- `static_bearer_env`
- `mcp_oauth_env`

For the full provider and MCP flow, see [docs/provider-integration.md](./docs/provider-integration.md).

## Provider Resources

On deploy or startup, ThruFlow reads the workspace, checks the state database for managed provider resource IDs, creates the Claude environment and shared memory store if they do not exist yet, persists those IDs, and reuses them on later runs. It also creates or reuses per-agent Anthropic vaults for MCP credentials when agents activate authenticated MCP servers.

## Runtime Model

- Messages from API, Slack, Telegram, heartbeats, and agent outputs are normalized into one internal shape.
- Routes reference prompt template files instead of embedding large prompts inline.
- Sessions attach the service-managed shared memory store with `read_write` access unless an agent overrides access mode.
- Agent outputs become new normalized messages, which lets route chaining implement the Researcher → Analyst → Brief Writer pipeline.
- Agent final outputs can be natural language; ThruFlow extracts `/mnt/memory/...` paths from them automatically.

The full runtime walk-through is in [docs/architecture.md](./docs/architecture.md).
- Routes can optionally mark a terminal output for Telegram reply delivery when the originating correlation came from Telegram.

## Artifact Handoff Convention

Agents should:
1. Write full outputs to `/mnt/memory/artifacts`.
2. Write compact handoff notes to `/mnt/memory/handoffs` when useful.
3. Mention written `/mnt/memory` paths in their final response.

Agents do not need to return JSON.

## Local Setup

1. Copy `.env.example` to `.env`.
2. Set `WORKSPACE_PATH` if you want a workspace other than `./workspace`.
3. Set `ANTHROPIC_API_KEY` for live provider calls.
4. Set `SLACK_BOT_TOKEN` or `TELEGRAM_BOT_TOKEN` if you want connector polling enabled.
5. Set any MCP secret env vars referenced by `workspace/tools.yaml`.
6. Install dependencies with `pip install -e .[dev]`.
7. Start the API with `uvicorn app.main:app --reload`.

`THRUFLOW_FAKE_CLAUDE=true` keeps the demo runnable without external API calls. Set it to `false` for live managed-agent execution.

## Docker Setup

Run:

```bash
docker compose up --build
```

The compose file mounts `./workspace` into `/app/workspace` as read-only and persists SQLite separately under `/app/data`.

Operational details and troubleshooting live in [docs/operations.md](./docs/operations.md).

## Docker Image

ThruFlow publishes a reusable container image to GitHub Container Registry.

- Image name: `ghcr.io/<repo-owner>/thruflow`
- The image namespace is based on the GitHub repository owner, so the same workflow works for both org-owned and user-owned repositories.
- The publish workflow is in [.github/workflows/docker-publish.yml](./.github/workflows/docker-publish.yml).

Example image reference:

```text
ghcr.io/YOUR_ORG_OR_USER/thruflow:latest
```

For reproducible deployments, prefer a commit-specific tag:

```text
ghcr.io/YOUR_ORG_OR_USER/thruflow:sha-<commit-sha>
```

The workflow publishes:

- `latest` on the default branch
- `sha-<commit-sha>` on every publish
- `vX.Y.Z` when pushing a matching git tag

By default, GHCR packages may be private. To let other repos pull the image easily, either make the package public in the GitHub Packages UI or authenticate when pulling the image.

## Deployment Repo Usage

The public ThruFlow repo can publish the base image, while a separate private deployment repo provides the workspace files and secrets.

That deploy repo can pull the published image instead of rebuilding it:

```yaml
services:
  thruflow:
    image: ghcr.io/YOUR_ORG_OR_USER/thruflow:latest
    env_file:
      - .env
    volumes:
      - ./workspace:/app/workspace:ro
      - thruflow-data:/app/data
    ports:
      - "8000:8000"
    environment:
      WORKSPACE_PATH: /app/workspace
      SQLITE_PATH: /app/data/thruflow.db

volumes:
  thruflow-data:
```

This split keeps the application image reusable while letting the deployment repo own environment-specific workspace config and secrets.

## Slack Setup

- Create a Slack app with `conversations.history` and `conversations.replies` scopes.
- Install the app and place the bot token in `SLACK_BOT_TOKEN`.
- Update `workspace/slack.yaml` with the channel IDs to poll.
- ThruFlow tracks per-channel and per-thread cursors in SQLite and routes new Slack messages through the same dispatcher as API and heartbeat messages.

## Telegram Setup

- Create a bot with BotFather and place the token in `TELEGRAM_BOT_TOKEN`.
- Add the allowed chat IDs to `workspace/telegram.yaml`.
- Start the service and send a text message to the bot.
- ThruFlow polls Telegram with `getUpdates`, stores the global `update_id` cursor in SQLite, normalizes new messages, and routes them through the same dispatcher as Slack, API, and heartbeats.
- If a terminal route has `reply.connector: telegram` and `reply.mode: final_output`, ThruFlow sends the final output back with `sendMessage` and uses `reply_to_message_id` when available.

Connector behavior and extension guidance are documented in [docs/connectors.md](./docs/connectors.md).

## GitHub Actions Deployment

The repository includes an example workflow at [.github/workflow-examples/deploy-managed-agents.yml.example](./.github/workflow-examples/deploy-managed-agents.yml.example). It shows how to run `python scripts/deploy_managed_agents.py` from GitHub Actions after checking out the repo and setting `ANTHROPIC_API_KEY`.

## Demo Walkthrough

Start the service and post an event:

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

The demo workspace runs this chain:

1. Researcher writes a research artifact under `/mnt/memory/artifacts/research/...` and mentions the path in the final response.
2. ThruFlow extracts the mentioned memory paths and emits them in the `agent_output` payload.
3. Analyst receives artifact and handoff paths, reads the files from shared memory, and writes an analysis artifact.
4. Brief Writer receives analysis artifact paths, reads from shared memory, and writes the final brief.

You can also run:

```bash
python scripts/run_demo.py
```

To exercise the Telegram path, configure `workspace/telegram.yaml`, start the service, and send the bot a text message from an allowed chat. The message will enter the same Researcher → Analyst → Brief Writer pipeline, and the configured terminal route can post the final brief back into Telegram.

## API Endpoints

- `GET /healthz`
- `POST /events`
- `POST /heartbeats/{heartbeat_id}/run`
- `GET /messages/{id}`
- `GET /sessions/{id}`
- `GET /routes`
- `GET /heartbeats`
- `POST /admin/reload-config`

Contributor guidance and extension notes are in [docs/development.md](./docs/development.md).
