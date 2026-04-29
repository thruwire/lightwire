# Workspace

ThruFlow loads its workspace from `WORKSPACE_PATH`, which defaults to `./workspace`.

## Layout

```text
workspace/
  agents/
    <agent_id>/
      config.yaml
      AGENT.md
  skills/
    <skill_id>/
      SKILL.md
  prompt_templates/
    <template_id>.md
  tools.yaml
  routes.yaml
  heartbeats.yaml
  slack.yaml
  telegram.yaml
```

## Agents

Each agent folder contains:

- `config.yaml`: runtime/provider-facing configuration
- `AGENT.md`: stable identity and operating instructions

The base model is externalized in `config.yaml`, not hardcoded in code or prompts. If you want a specific model for an agent, set `model:` there explicitly.

`AGENT.md` should describe the durable responsibilities of the agent. Route-specific tasks should not go there; they belong in prompt templates.

Important fields in `workspace/agents/<agent_id>/config.yaml`:

- `agent_id`
- `enabled`
- `display_name`
- `description`
- `provider`
- `model`
- `memory.access`
- `skills`
- `tools`

## Skills

Skills are reusable instruction fragments shared across agents.

Each skill folder contains:

- `SKILL.md`: reusable instructions with required YAML frontmatter

The YAML frontmatter is the source of truth for skill identity and triggering metadata. At minimum it should define:

- `name`
- `description`

At runtime, ThruFlow appends enabled skill instructions after the agent’s `AGENT.md` content when building the system prompt for a session.

## Prompt Templates

Prompt templates live under `workspace/prompt_templates/*.md`.

Routes and heartbeats reference them by workspace-relative path, for example:

- `prompt_templates/api_to_research.md`
- `prompt_templates/research_to_analysis.md`

Templates are rendered with Jinja-style variables. Supported context includes:

- `message`
- `payload`
- `metadata`
- `correlation_id`
- `parent_message_id`

If a route references a missing template file, ThruFlow raises a clear `FileNotFoundError`.

The normal handoff convention is:

- upstream agents write durable files under `/mnt/memory`
- upstream agents mention those `/mnt/memory/...` paths in their final response
- ThruFlow extracts the paths and places them in `payload.artifacts`, `payload.handoffs`, and `payload.memory_paths`
- downstream templates primarily read from `payload.artifacts` and `payload.handoffs`

## Tools

`workspace/tools.yaml` is the shared tool registry.

It contains:

- built-in tool defaults
- remote MCP server definitions
- auth references for MCP credentials

Agent configs then activate the specific built-in tools and MCP servers they need. This keeps infrastructure config at the workspace level and permissions at the agent level.

For the standard memory-artifact flow, agents that are expected to create handoff files should usually activate at least:

- `read`
- `write`

`bash` is also commonly useful. Agents that do not need external research should generally not activate `web_search` or `web_fetch`.

## Routes

`workspace/routes.yaml` wires normalized messages to agent sessions.

Each route defines:

- `id`
- `enabled`
- `match`
- `target`
- optional `reply`
- optional `require_artifacts`

`match` currently supports:

- `source`
- `type`
- optional `agent_id` for matching agent output messages

`target` specifies:

- `agent_id`
- `prompt_template`

Optional `reply` is currently used for final Telegram replies.

Optional `require_artifacts` can block downstream routing when an agent fails to mention any artifact paths in its final response.

## Heartbeats

`workspace/heartbeats.yaml` defines scheduled prompts that emit normalized heartbeat messages and then dispatch them through the normal routing path.

## Connector Config

- `workspace/slack.yaml` controls Slack Socket Mode by default, with polling fallback available
- Slack config supports multiple channels, DMs, explicit-address rules, and `channel_name` to `channel_id` resolution at startup
- `workspace/telegram.yaml` controls Telegram polling and allowed chat IDs

Both connectors are optional. Missing `telegram.yaml` loads as disabled.

For a field-by-field reference for every workspace YAML file, see [config-formats.md](./config-formats.md).
