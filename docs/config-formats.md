# Config Formats

This page documents every workspace YAML file and the main environment variables used by ThruFlow.

## Environment Variables

`.env.example` shows the current env surface:

- `ANTHROPIC_API_KEY`: provider API key for live Claude calls
- `ANTHROPIC_BASE_URL`: provider API base URL, defaults to `https://api.anthropic.com/v1`
- `ANT_BIN`: Anthropic CLI binary path, defaults to `ant`
- `SLACK_BOT_TOKEN`: Slack bot token for Socket Mode, replies, and polling fallback
- `SLACK_APP_TOKEN`: Slack Socket Mode app-level token
- `TELEGRAM_BOT_TOKEN`: Telegram Bot API token
- `WORKSPACE_PATH`: workspace root, defaults to `./workspace`
- `SQLITE_PATH`: SQLite database path
- `THRUFLOW_DELETE_COMPLETED_SESSIONS`: whether completed remote Anthropic sessions are deleted after ThruFlow captures the final output, defaults to `true`
- MCP secret env vars referenced from `workspace/tools.yaml`

## `workspace/tools.yaml`

Purpose:
- declare shared built-in tool defaults
- declare shared remote MCP servers
- declare auth references for MCP credentials

Shape:

```yaml
built_in:
  bash:
    enabled: true
    permission_policy: always_allow
  read:
    enabled: true
    permission_policy: always_allow
  write:
    enabled: true
    permission_policy: always_allow
  web_search:
    enabled: true
    permission_policy: always_allow

mcp_servers:
  external_research:
    enabled: true
    type: url
    url: https://example.com/mcp
    permission_policy: always_allow
    auth:
      type: static_bearer_env
      token_env_var: EXTERNAL_RESEARCH_MCP_TOKEN
```

Built-in tool fields:

- `enabled`: whether the tool is available in the registry
- `permission_policy`: currently `always_allow` or `always_ask`

Common built-in tools used by this repo's artifact-handoff pattern:

- `read`
- `write`
- `bash`
- `web_search`
- `web_fetch`

MCP server fields:

- `enabled`
- `type`: current repo examples use `url`
- `url`
- `permission_policy`
- `auth`

Supported auth types:

- `none`
- `static_bearer_env`
- `mcp_oauth_env`

## `workspace/routes.yaml`

Purpose:
- define control-plane routing from normalized messages to agent sessions

Shape:

```yaml
routes:
  - id: api_to_research
    enabled: true
    match:
      source: api
      type: message.created
    target:
      agent_id: researcher
      prompt_template: prompt_templates/api_to_research.md
    require_artifacts: false
```

Route fields:

- `id`: unique route key
- `enabled`
- `match`
- `target`
- optional `reply`
- optional `require_artifacts`

`match` fields:

- `source`: `api`, `slack`, `telegram`, `heartbeat`, or `agent_output`
- `type`: currently `message.created`, `heartbeat.tick`, or `agent.completed`
- optional `agent_id`: used when matching `agent_output`

`target` fields:

- `agent_id`
- `prompt_template`: workspace-relative template path

Optional `reply` fields:

- `connector`
- `mode`

Current Telegram example:

```yaml
reply:
  connector: telegram
  mode: final_output
```

`require_artifacts` behavior:

- if `false` or omitted, path extraction still happens automatically, but missing artifacts do not block the chain
- if `true`, missing extracted artifact paths mark the output invalid and stop downstream routing

## `workspace/heartbeats.yaml`

Purpose:
- define internal scheduled prompts

Shape:

```yaml
heartbeats:
  - id: research_scout
    enabled: true
    interval_seconds: 3600
    target:
      agent_id: researcher
      prompt_template: prompt_templates/heartbeat_research_scout.md
```

Fields:

- `id`
- `enabled`
- `interval_seconds`
- `target.agent_id`
- `target.prompt_template`

## `workspace/slack.yaml`

Purpose:
- configure the Slack connector, with Socket Mode as the default

Shape:

```yaml
enabled: true
mode: socket

channels:
  - channel_id: C123456
    include_threads: true
  - channel_name: ai-playground
    include_threads: true

behavior:
  requires_mention: true
  allow_dms: true
  prefixes:
    - "!tf"

socket:
  reconnect: true
  ack_timeout_seconds: 3

polling:
  enabled: false
  poll_interval_seconds: 30

ignore_bot_messages: true
send_replies: true
```

Fields:

- `enabled`
- `mode`: `socket`, `polling`, or `webhook`
- `channels`
- `behavior`
- `socket`
- `polling`
- `ignore_bot_messages`
- `send_replies`

Notes:

- If `mode` is missing, it defaults to `socket`.
- `webhook` is reserved for future use and is not implemented in this repo yet.
- Polling is fallback-only and should not be run as the primary real-time ingestion path.
- Channel entries may specify `channel_id` or `channel_name`. Names are resolved to IDs at startup, then normalized to `channel_id` in memory.
- Channel names are cached in `/app/data/slack_channels.json` in containerized deployments because `SQLITE_PATH` defaults under `/app/data`.
- DMs are controlled separately by `behavior.allow_dms`.
- Channel messages only trigger when explicitly addressed by mention or configured prefix.

`socket` fields:

- `reconnect`
- `ack_timeout_seconds`

`polling` fields:

- `enabled`
- `poll_interval_seconds`

Channel fields:

- `channel_id`
- `channel_name`
- `include_threads`

Behavior fields:

- `requires_mention`
- `allow_dms`
- `prefixes`

## `workspace/telegram.yaml`

Purpose:
- configure the Telegram polling connector

Shape:

```yaml
enabled: true
poll_interval_seconds: 10
allowed_chats:
  - chat_id: "123456789"
    route_key: default
ignore_bot_messages: true
send_replies: true
```

Fields:

- `enabled`
- `poll_interval_seconds`
- `allowed_chats`
- `ignore_bot_messages`
- `send_replies`

Allowed chat fields:

- `chat_id`
- `route_key`

## `workspace/agents/<agent_id>/config.yaml`

Purpose:
- declare runtime settings and tool activation for one agent

Shape:

```yaml
agent_id: researcher
enabled: true
display_name: Researcher
description: Researches a topic and writes structured findings.
provider: claude_managed_agents
model: claude-sonnet-4-6

memory:
  access: read_write

skills:
  - structured_notes

tools:
  built_in:
    - bash
    - read
    - write
    - web_search
  mcp:
    external_research:
      allow:
        - search_documents
        - fetch_document
```

Fields:

- `agent_id`
- `enabled`
- `display_name`
- `description`
- `provider`
- `model`
- `memory.access`
- `skills`
- `tools.built_in`
- `tools.mcp`

## `workspace/agents/<agent_id>/AGENT.md`

Purpose:
- provide the stable system prompt for the agent

Required convention:

- describe the durable role of the agent
- explain the shared memory output convention
- tell the agent to use the built-in `write` tool for durable `/mnt/memory` outputs
- tell the agent not to read directories like `/mnt/memory`
- instruct the agent to mention every `/mnt/memory/...` path it writes
- allow natural language output
- do not require JSON output

## `workspace/skills/<skill_id>/SKILL.md`

Purpose:
- provide reusable instruction text appended to agent instructions when enabled
- define skill identity and trigger metadata in YAML frontmatter

Shape:

```md
---
name: structured-notes
description: Use when the task requires structured synthesis, concise handoffs, or easy-to-scan outputs for downstream agents.
---

Use this skill when the task requires structured synthesis or handoffs.
```

Required frontmatter fields:

- `name`
- `description`

Expected behavior:

- `name` should be lowercase and hyphenated in the file
- ThruFlow converts that name into its internal skill key by replacing `-` with `_`
- `description` should describe both what the skill does and when it should be used

The Markdown body below the frontmatter is the reusable execution guidance appended to agent instructions.

## `workspace/prompt_templates/*.md`

Purpose:
- define route-specific task prompts

Template context:

- `message`
- `payload`
- `metadata`
- `correlation_id`
- `parent_message_id`

Current convention:

- downstream prompts should primarily consume `payload.artifacts` and `payload.handoffs`
- prompts can still mention `payload.content` or `payload.summary` for human context if needed
- agents should mention every written `/mnt/memory/...` path in their final response
