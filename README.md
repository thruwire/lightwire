# ThruFlow

ThruFlow is an orchestrator of agent harnesses, currently supporting Claude Managed Agent harnesses while remaining extensible to other managed-agent harnesses over time.

Unlike local multi-agent setups that require your own hardware, ThruFlow relies on Claude Managed Agents so the heavy execution runs in Anthropic’s infrastructure.

## Documentation

- [Docs Index](./docs/README.md)
- [Architecture](./docs/architecture.md)
- [Workspace](./docs/workspace.md)
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

The full runtime walk-through is in [docs/architecture.md](./docs/architecture.md).
- Routes can optionally mark a terminal output for Telegram reply delivery when the originating correlation came from Telegram.

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

`.github/workflows/deploy-managed-agents.yml` installs the project and runs `python scripts/deploy_managed_agents.py` on pushes to `main` or manual dispatch. The deploy script reads `workspace/agents/*`, uses each `AGENT.md` as the stable instruction source, ensures the service-managed Claude environment and shared memory store exist in the state DB, ensures Anthropic vaults exist for authenticated MCP servers, and then deploys or updates provider-backed agents from workspace config.

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

1. Researcher writes structured findings.
2. Analyst evaluates tradeoffs and risks.
3. Brief Writer produces a concise executive brief.

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
