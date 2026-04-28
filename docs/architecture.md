# Architecture

ThruFlow is a lightweight orchestrator for agent harnesses. Today it targets Claude Managed Agents, but the internal runtime is structured around provider-neutral concepts:

- normalized messages
- route matching
- session execution
- shared provider-managed memory
- connector cursors and scheduler state

## Runtime Flow

At runtime, every inbound event becomes a `NormalizedMessage`:

- API events from `POST /events`
- Slack messages from the polling connector
- Telegram messages from the polling connector
- heartbeat ticks from the internal scheduler
- agent outputs emitted after session completion

That message then moves through the same path:

1. Persist the message in SQLite.
2. Resolve matching routes from `workspace/routes.yaml`.
3. Render the route prompt template with normalized message data.
4. Start a managed-agent session with:
   - agent instructions from `AGENT.md`
   - enabled skill instructions from `SKILL.md`
   - the rendered route prompt
   - shared memory attached
   - configured built-in and MCP tools
5. Persist the session and agent output.
6. Convert the output back into a normalized `agent_output` message.
7. Feed that message back into the dispatcher so downstream routes can run.

This recursive output-to-message loop gives ThruFlow simple DAG chaining without introducing a separate graph engine.

## Main Modules

- `app/main.py`: FastAPI app creation, startup/shutdown, and service wiring
- `app/config.py`: workspace and environment-backed config loading
- `app/models.py`: shared Pydantic models for runtime state
- `app/routing/`: route matching and prompt rendering
- `app/workers/`: dispatch, session execution, and output normalization
- `app/connectors/`: polling connectors for external sources
- `app/scheduler/heartbeat.py`: internal interval scheduler
- `app/claude/`: Claude Managed Agent provider adapter
- `app/db/sqlite.py`: SQLite-backed repository implementations

## Persistence Model

SQLite persists orchestration state, not shared agent working files.

The main tables are:

- `messages`: normalized inbound and internal events
- `sessions`: managed-agent session records
- `agent_outputs`: stored agent completion content
- `heartbeats`: next and last run state
- `connector_cursors`: Slack and Telegram polling cursors
- `provider_state`: service-managed provider IDs such as environment, memory store, and vaults

Shared artifacts and handoffs are expected to live in the provider-managed memory store mounted into sessions under `/mnt/memory`.

## Extension Model

The repo is designed so future managed-agent harnesses can be added by replacing or extending the provider adapter layer while keeping:

- normalized message handling
- route matching
- workspace conventions
- repository interfaces
- connector logic

The current Claude-specific implementation is concentrated under `app/claude/`.
