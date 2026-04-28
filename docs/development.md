# Development

## Test Coverage

The repo includes pytest coverage for:

- workspace config loading
- route matching
- prompt rendering
- dispatcher chaining
- heartbeat scheduling
- Slack polling and cursor logic
- Telegram polling, normalization, and reply flow
- repository CRUD
- Claude provider resource reuse in fake mode

Run tests with:

```bash
python -m pytest -q
```

## Design Principles

When extending ThruFlow, keep these constraints in mind:

- normalize source-specific events early
- keep routing provider-neutral
- isolate provider-specific translation in adapter modules
- keep shared workspace files declarative
- persist enough local state to resume safely after restarts

## Adding Routes

To add a route:

1. create or reuse a prompt template under `workspace/prompt_templates/`
2. add a route entry to `workspace/routes.yaml`
3. point it at an existing agent
4. reload config or restart the service

## Adding Agents

To add an agent:

1. create `workspace/agents/<agent_id>/config.yaml`
2. create `workspace/agents/<agent_id>/AGENT.md`
3. optionally attach skills and tools
4. run `python scripts/deploy_managed_agents.py`

## Adding MCP Servers

To add a generic third-party HTTP MCP server:

1. declare it in `workspace/tools.yaml`
2. choose an auth pattern
3. provide referenced secret env vars
4. activate it in the relevant agent configs
5. redeploy agents or restart the service

## Adding New Provider Support

The easiest seam for another managed-agent backend is:

- add a provider adapter package parallel to `app/claude/`
- keep the existing routing and repository layers
- translate workspace tool config into the new provider’s tool model
- make session execution return the same internal result shape
