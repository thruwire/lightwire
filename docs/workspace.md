# Workspace

LightWire loads its versionable control-plane definition from `WORKSPACE_PATH`, which defaults to `./workspace`.

The workspace describes how your multi-agent system is coordinated:

- which agents exist
- which prompts they receive
- which tools they can use
- how messages are routed
- which heartbeats fire
- which connectors are enabled

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

- `config.yaml`: runtime and provider-facing configuration
- `AGENT.md`: durable identity and operating instructions

Use `AGENT.md` for stable role definition. Put route-specific work in prompt templates, not in the agent file.

Important fields in `workspace/agents/<agent_id>/config.yaml` include:

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

Skills are reusable instruction packages shared across agents.

Each skill folder contains:

- `SKILL.md`: skill content with required YAML frontmatter

At minimum, the frontmatter should define:

- `name`
- `description`

In live Claude Managed Agent deploys, LightWire uploads workspace skills as Anthropic custom skills and attaches the resulting `skill_*` IDs to agents that reference them. In fake mode, the skill body is inlined into the system prompt so the orchestration path remains testable.

## Prompt Templates

Prompt templates live under `workspace/prompt_templates/*.md`.

Routes and heartbeats reference them by workspace-relative path, for example:

- `prompt_templates/api_to_research.md`
- `prompt_templates/research_to_analysis.md`

Templates are rendered with Jinja-style variables. Common context includes:

- `message`
- `payload`
- `metadata`
- `correlation_id`
- `parent_message_id`
- `upstream_outputs_text`

The normal handoff pattern is explicit:

- an upstream agent returns output content
- LightWire captures that output as routed state
- downstream prompts consume the routed upstream context

## Tools

`workspace/tools.yaml` is the shared tool registry.

It defines:

- built-in tool defaults
- remote MCP server definitions
- auth references for MCP credentials

Agent configs then opt into the specific tools and MCP servers they need. This keeps infrastructure shared at the workspace level and permissions scoped at the agent level.

## Routes

`workspace/routes.yaml` is the core orchestration file.

Each route declares:

- what message shape it matches
- which agent should run
- which prompt template to render
- whether a connector reply should be emitted at the terminal step

Current route matching supports:

- `source`
- `type`
- optional `agent_id` for matching agent outputs

Direct routed outputs are the normal chaining mechanism. `require_artifacts` remains only for backward compatibility.

## Heartbeats

`workspace/heartbeats.yaml` defines scheduled prompts.

A heartbeat emits a normalized `heartbeat.tick` message, then enters the same routing path as API, Slack, Telegram, and agent-output events.

## Connector Config

- `workspace/slack.yaml` configures Slack Socket Mode by default, with polling fallback available
- `workspace/telegram.yaml` configures Telegram polling and allowed chat behavior

Both connectors are optional.

For the full field-by-field schema, see [Config Formats](./config-formats.md).
