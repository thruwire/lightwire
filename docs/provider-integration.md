# Provider Integration

ThruFlow currently targets Claude Managed Agents through the adapter in `app/claude/`.

Managed-agent provisioning in this repo uses the Anthropic `ant` CLI. ThruFlow uses `ant` to create or update environments, memory stores, vaults, credentials, and agent definitions, then persists the resulting provider IDs in SQLite for later reuse.

## Provider Resource Ownership

ThruFlow manages provider resource IDs itself instead of requiring them in `.env`.

On startup or deploy:

1. read the workspace
2. look up existing provider state in SQLite
3. create missing resources
4. persist returned IDs
5. reuse them on later runs

Current managed resources:

- Claude environment
- shared memory store
- per-agent vaults for MCP credentials
- vault credentials for authenticated MCP servers

These IDs are stored in the `provider_state` table.

## Endpoint Availability

Provisioning no longer assumes undocumented direct REST paths for environments or vaults. Instead, ThruFlow relies on the Anthropic CLI surface for those resources.

For live operation you still need:

- `ANTHROPIC_API_KEY`
- the `ant` CLI on `PATH`, or `ANT_BIN` pointing to it
- a provider environment where managed-agent sessions are available

If you want fully mocked behavior for tests or demos, set `THRUFLOW_FAKE_CLAUDE=true` explicitly.

## Session Construction

For each route dispatch, ThruFlow builds a provider session request from:

- agent instructions from `AGENT.md`
- enabled `SKILL.md` instructions
- rendered route prompt template
- shared memory store attachment
- agent-specific tool activation
- MCP vault attachments when required

The provider request also includes route and correlation metadata so provider-side activity can be tied back to local orchestration state.

Agents are allowed to respond in natural language. ThruFlow does not require a strict JSON completion contract. The only shared convention is that agents should mention every written `/mnt/memory/...` path in their final response so the orchestrator can extract and forward those paths.

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

The repo reads secret values from environment variables, creates or reuses provider vault credentials, and then attaches the resulting vault IDs at session start.

## Fake Mode

`THRUFLOW_FAKE_CLAUDE=true` is available as an explicit testing and demo switch when you do not want live provider calls.

In fake mode:

- environments and memory stores get mock IDs
- sessions produce deterministic mock outputs
- vault creation and credential creation are simulated

This makes the full orchestration pipeline testable without external dependencies.
