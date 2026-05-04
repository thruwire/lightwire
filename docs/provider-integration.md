# Provider Integration

LightWire currently targets Claude Managed Agents through the adapter in `app/claude/`.

This is the execution boundary in the system:

- managed agents are the execution layer
- LightWire is the coordination layer

LightWire does not run the reasoning loop locally. It prepares session inputs, attaches the right tools and credentials, starts provider-side sessions, captures the results, and routes them onward.

## Deployment Model

Managed-agent provisioning in this repo uses the Anthropic `ant` CLI.

The deploy/apply path is explicit and mutating:

1. read the workspace
2. load cached provider state from SQLite
3. verify cached IDs remotely
4. create or repair missing resources
5. update agent definitions
6. persist the resulting provider IDs locally

Runtime startup is the non-mutating path:

1. read the workspace
2. load cached provider state from SQLite
3. fail fast if the required deployment state is missing

This keeps provider changes out of ordinary app startup.

## Provider Resource Ownership

LightWire manages provider resource IDs itself rather than requiring them in `.env`.

For deployment repos that share the same Anthropic account or project, set `LIGHTWIRE_WORKSPACE_ID` explicitly so provider-side environments, memory stores, and shared MCP vault resources are namespaced per deployment.

Current managed resources include:

- Claude environment
- shared memory store
- custom skills referenced by the workspace
- shared workspace MCP vaults
- vault credentials for authenticated MCP servers

These IDs are stored in the `provider_state` table.

## Session Construction

For each route dispatch, LightWire builds a provider session request from:

- agent instructions from `AGENT.md`
- skill attachments resolved from workspace `SKILL.md` packages
- the rendered route prompt template
- any provider-side resources required by the adapter
- agent-specific tool activation
- MCP vault attachments when needed

Route and correlation metadata are included so provider-side activity can be tied back to local orchestration state.

Agents can respond in natural language. LightWire captures the result directly and forwards it as routed output data.

## Tools And MCP

Tool configuration stays provider-neutral until the adapter compiles it.

Workspace-level config defines:

- built-in tool defaults
- remote MCP servers
- auth configuration

Agent-level config defines:

- which built-in tools to enable
- which MCP servers to activate
- which MCP tools to allow from each server

The Claude adapter translates that into provider-specific payloads such as built-in toolsets, `mcp_servers`, and MCP tool allowlists.

## MCP Auth Patterns

Current auth patterns are:

- `static_bearer_env`
- `mcp_oauth_env`
- `mcp_oauth_client_credentials_env`

`mcp_oauth_client_credentials_env` is the preferred path for OAuth-backed remote MCP servers when the token endpoint returns `access_token`, `refresh_token`, and `expires_in`.

In that mode, LightWire:

1. reads stable OAuth client settings from env
2. mints the initial token pair during deploy
3. writes an Anthropic vault credential of type `mcp_oauth`
4. relies on Anthropic-managed refresh after that

For shared services, LightWire creates one shared MCP vault per workspace and one credential per configured MCP server, then reuses those resources across agents.

## Endpoint Availability

Provisioning relies on the Anthropic CLI surface rather than undocumented direct REST paths for environments or vaults.

For live operation you still need:

- `ANTHROPIC_API_KEY`
- the `ant` CLI on `PATH`, or `ANT_BIN` pointing to it
- a provider environment where managed-agent sessions are available

The wrapper entrypoint is `python scripts/deploy_managed_agents.py`. The deployment logic itself lives in `app/deploy/` and `app/claude/`.

## Fake Mode

`LIGHTWIRE_FAKE_CLAUDE=true` is available for tests and demos when you do not want live provider calls.

In fake mode:

- environments and memory stores get mock IDs
- sessions produce deterministic mock outputs
- vault and credential creation are simulated

That keeps the control-plane path testable without external dependencies.
