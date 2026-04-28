# Provider Integration

ThruFlow currently targets Claude Managed Agents through the adapter in `app/claude/`.

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

## Session Construction

For each route dispatch, ThruFlow builds a provider session request from:

- agent instructions from `AGENT.md`
- enabled `SKILL.md` instructions
- rendered route prompt template
- shared memory store attachment
- agent-specific tool activation
- MCP vault attachments when required

The provider request also includes route and correlation metadata so provider-side activity can be tied back to local orchestration state.

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

`THRUFLOW_FAKE_CLAUDE=true` keeps local development and CI runnable without live provider calls.

In fake mode:

- environments and memory stores get mock IDs
- sessions produce deterministic mock outputs
- vault creation and credential creation are simulated

This makes the full orchestration pipeline testable without external dependencies.
