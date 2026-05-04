# Architecture

LightWire is the coordination layer for a managed-agent system. It does not host the agentic loop itself. Managed agents handle reasoning, tool use, and iterative execution in provider infrastructure. LightWire handles the control plane: message normalization, route matching, prompt rendering, session dispatch, output capture, and follow-on routing.

## Runtime Model

The runtime is built around a small set of provider-neutral concepts:

- normalized messages
- route matching
- prompt rendering
- managed-agent session execution
- captured routed outputs
- connector cursors and heartbeat state

That separation lets the execution layer stay remote while the coordination layer stays local and inspectable.

## Message Flow

Every inbound event becomes a `NormalizedMessage`.

Current sources include:

- API events from `POST /events`
- Slack connector events
- Telegram connector events
- heartbeat ticks from the scheduler
- agent outputs emitted after a session completes

Each message then follows the same path:

1. Persist the normalized message in SQLite.
2. Match routes from `workspace/routes.yaml`.
3. Render the route prompt template with message context.
4. Start the target managed-agent session with:
   - agent instructions from `AGENT.md`
   - enabled skill instructions from `SKILL.md`
   - the rendered route prompt
   - configured built-in tools and MCP servers
5. Persist the session record and raw agent output.
6. Convert the result into a normalized `agent_output` message.
7. Feed that output back into the dispatcher so downstream routes can run.

This gives LightWire explicit multi-agent chaining without requiring shared working directories or implicit memory conventions.

## Direct Routed Outputs

Direct routed outputs are the normal handoff mechanism.

- an upstream agent returns the output content itself
- LightWire captures that output as orchestration state
- downstream routes receive structured upstream context

Agents do not need to know provider filesystem paths. They do not need to emit a strict JSON contract just to hand off work to the next step.

## Main Modules

- `app/main.py`: FastAPI app creation, startup, shutdown, and service wiring
- `app/config.py`: workspace and environment-backed config loading
- `app/models.py`: shared runtime models
- `app/routing/`: route matching and prompt rendering
- `app/workers/`: dispatch, session execution, and output normalization
- `app/connectors/`: external event ingestion and reply adapters
- `app/scheduler/heartbeat.py`: internal heartbeat scheduler
- `app/claude/`: Claude Managed Agent provider adapter
- `app/db/sqlite.py`: SQLite-backed repositories

## Persistence Model

SQLite stores orchestration state, not the shared execution environment for agents.

Key tables include:

- `messages`: inbound and internal normalized events
- `sessions`: managed-agent session records
- `agent_outputs`: captured completion content and summaries
- `heartbeats`: next and last run state
- `connector_cursors`: Slack and Telegram resume state
- `provider_state`: cached provider resource IDs such as environments, memory stores, vaults, and credentials

## Provider Boundary

The current provider adapter targets Claude Managed Agents, but the routing model is not Claude-specific.

LightWire owns:

- route evaluation
- template rendering
- connector ingestion
- output capture
- orchestration persistence

The managed-agent platform owns:

- model execution
- tool invocation
- iterative reasoning
- provider-side session lifecycle

## Extension Model

To support another managed-agent backend, the main seam is the provider adapter layer. The rest of the system can stay intact:

- normalized message handling
- route matching
- workspace conventions
- repository interfaces
- connector logic

The current Claude-specific implementation is concentrated under `app/claude/`.
