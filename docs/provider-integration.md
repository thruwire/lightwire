# Provider Integration

ThruFlow currently targets Claude Managed Agents through the adapter in `app/claude/`.

Managed-agent provisioning in this repo uses the Anthropic `ant` CLI. ThruFlow uses `ant` to create or update environments, memory stores, vaults, credentials, and agent definitions, then persists the resulting provider IDs in SQLite for later reuse.

## Provider Resource Ownership

ThruFlow manages provider resource IDs itself instead of requiring them in `.env`.
For deployment repos that share the same Anthropic account/project, `THRUFLOW_WORKSPACE_ID` should be set explicitly so provider-side environments, memory stores, and shared MCP vault resources are uniquely namespaced.

Deploy/apply is the mutating path:

1. read the workspace
2. look up existing provider state in SQLite
3. verify cached IDs remotely
4. create or repair missing resources
5. persist returned IDs
6. update agent definitions

Runtime startup is the non-mutating path:

1. read the workspace
2. load cached provider state from SQLite
3. fail fast if required deployment state is missing

Current managed resources:

- Claude environment
- shared memory store
- shared workspace MCP vaults
- vault credentials for authenticated MCP servers, keyed by MCP server

These IDs are stored in the `provider_state` table.

## Endpoint Availability

Provisioning no longer assumes undocumented direct REST paths for environments or vaults. Instead, ThruFlow relies on the Anthropic CLI surface for those resources.

For live operation you still need:

- `ANTHROPIC_API_KEY`
- the `ant` CLI on `PATH`, or `ANT_BIN` pointing to it
- a provider environment where managed-agent sessions are available

The deploy wrapper script is `python scripts/deploy_managed_agents.py`, but the deployment logic itself lives in app code under `app/deploy/` and `app/claude/`. That keeps deployment testable and reusable without forcing startup to auto-deploy.

If you want fully mocked behavior for tests or demos, set `THRUFLOW_FAKE_CLAUDE=true` explicitly.

## Session Construction

For each route dispatch, ThruFlow builds a provider session request from:

- agent instructions from `AGENT.md`
- enabled `SKILL.md` instructions
- rendered route prompt template
- any provider-side resources required by the adapter
- agent-specific tool activation
- MCP vault attachments when required

The provider request also includes route and correlation metadata so provider-side activity can be tied back to local orchestration state.

Agents are allowed to respond in natural language. ThruFlow does not require a strict JSON completion contract. The orchestrator captures that output directly and forwards it as routed output data.

## Tools And MCP

The repo keeps tool configuration provider-neutral until the adapter compiles it.

Workspace-level config defines:

- built-in tool defaults
- remote MCP servers
- auth configuration

Agent-level config defines:

- which built-in tools to enable
- which MCP servers to activate
- which MCP tools to allow from each server

The Claude adapter translates that into provider-specific payloads such as:

- built-in toolsets
- `mcp_servers`
- MCP tool allowlists

## Generic Third-Party MCP Servers

ThruFlow is designed to support any third-party MCP server that is reachable over HTTP and can be described in `workspace/tools.yaml`.

Current auth patterns:

- `static_bearer_env`
- `mcp_oauth_env`
- `mcp_oauth_client_credentials_env`

`mcp_oauth_env` is a compatibility path when you already have token material available externally.

`mcp_oauth_client_credentials_env` is the preferred pattern for OAuth-backed remote MCP servers in managed-agent deployments. With that mode, ThruFlow reads only the stable OAuth client configuration from env, mints the initial access and refresh tokens during deploy, writes an Anthropic vault credential of type `mcp_oauth`, and relies on Anthropic-managed refresh after that.

The repo reads secret values from environment variables, creates or reuses provider vault credentials, and then attaches the resulting vault IDs at session start.

For the common shared-service case, ThruFlow now creates one shared MCP vault per workspace and one credential per configured MCP server, then reuses that shared vault across agents that activate the same server.

## Fake Mode

`THRUFLOW_FAKE_CLAUDE=true` is available as an explicit testing and demo switch when you do not want live provider calls.

In fake mode:

- environments and memory stores get mock IDs
- sessions produce deterministic mock outputs
- vault creation and credential creation are simulated

This makes the full orchestration pipeline testable without external dependencies.
