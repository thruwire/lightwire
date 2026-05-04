# Config Formats

This page documents every workspace YAML file and the main environment variables used by LightWire.


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
- `LIGHTWIRE_DELETE_COMPLETED_SESSIONS`: whether completed remote Anthropic sessions are deleted after LightWire captures the final output, defaults to `true`
- MCP secret env vars referenced from `workspace/tools.yaml`

Example:

```dotenv
ANTHROPIC_API_KEY=your-provider-key
ANT_BIN=ant
WORKSPACE_PATH=./workspace
SQLITE_PATH=./data/lightwire.db
SLACK_BOT_TOKEN=xoxb-example
SLACK_APP_TOKEN=xapp-example
TELEGRAM_BOT_TOKEN=123456:example
CATALYST_DOCS_MCP_TOKEN=replace-me
```

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

Detailed example:

```yaml
built_in:
  bash:
    enabled: true
    permission_policy: always_allow
  read:
    enabled: true
    permission_policy: always_allow
  write:
    enabled: false
    permission_policy: always_ask
  web_fetch:
    enabled: true
    permission_policy: always_allow
  web_search:
    enabled: true
    permission_policy: always_allow

mcp_servers:
  catalyst_docs:
    enabled: true
    type: url
    url: ${CATALYST_DOCS_MCP_URL:-https://mcp.example.net/docs}
    permission_policy: always_allow
    auth:
      type: static_bearer_env
      token_env_var: CATALYST_DOCS_MCP_TOKEN

  ledger_api:
    enabled: ${LEDGER_MCP_ENABLED:-false}
    type: url
    url: ${LEDGER_MCP_URL:-https://mcp.example.net/ledger}
    permission_policy: always_allow
    auth:
      type: mcp_oauth_client_credentials_env
      token_endpoint: ${LEDGER_OAUTH_TOKEN_ENDPOINT}
      client_id_env_var: LEDGER_MCP_CLIENT_ID
      client_secret_env_var: LEDGER_MCP_CLIENT_SECRET
      token_endpoint_auth_method: client_secret_post
```

Built-in tool fields:

- `enabled`: whether the tool is available in the registry
- `permission_policy`: currently `always_allow` or `always_ask`

Common built-in tools used by this repo's direct-routing pattern:

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
- `mcp_oauth_client_credentials_env`

`mcp_oauth_env` expects access-token material to already exist in env at deploy time.

`mcp_oauth_client_credentials_env` expects stable OAuth client settings in env and lets LightWire mint the initial token pair during deploy:

```yaml
mcp_servers:
  thruwire:
    enabled: true
    type: url
    url: ${THRUWIRE_MCP_URL}
    permission_policy: always_allow
    auth:
      type: mcp_oauth_client_credentials_env
      token_endpoint: ${THRUWIRE_OAUTH_TOKEN_ENDPOINT}
      client_id_env_var: THRUWIRE_MCP_CLIENT_ID
      client_secret_env_var: THRUWIRE_MCP_CLIENT_SECRET
      token_endpoint_auth_method: ${THRUWIRE_OAUTH_TOKEN_ENDPOINT_AUTH_METHOD:-client_secret_post}
```

For this mode to support Anthropic automatic refresh, the token endpoint must return `access_token`, `refresh_token`, and `expires_in`.

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

Detailed example:

```yaml
routes:
  - id: api_to_triage
    enabled: true
    match:
      source: api
      type: message.created
    target:
      agent_id: triage
      prompt_template: prompt_templates/api_to_triage.md

  - id: slack_to_triage
    enabled: true
    match:
      source: slack
      type: message.created
    target:
      agent_id: triage
      prompt_template: prompt_templates/slack_to_triage.md

  - id: triage_to_research
    enabled: true
    match:
      source: agent_output
      type: agent.completed
      agent_id: triage
    target:
      agent_id: researcher
      prompt_template: prompt_templates/triage_to_research.md

  - id: research_to_reporter
    enabled: true
    match:
      source: agent_output
      type: agent.completed
      agent_id: researcher
    target:
      agent_id: reporter
      prompt_template: prompt_templates/research_to_reporter.md
    reply:
      connector: slack
      mode: final_output
```

Route fields:

- `id`: unique route key
- `enabled`
- `match`
- `target`
- optional `reply`
- optional `require_artifacts` for backward compatibility

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

`require_artifacts` is retained for backward compatibility, but the normal routing path now uses direct routed outputs instead of memory-path extraction.

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

Detailed example:

```yaml
heartbeats:
  - id: morning_briefing
    enabled: true
    interval_seconds: 3600
    target:
      agent_id: researcher
      prompt_template: prompt_templates/morning_briefing.md

  - id: stale_ticket_scan
    enabled: false
    interval_seconds: 21600
    target:
      agent_id: triage
      prompt_template: prompt_templates/stale_ticket_scan.md
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

Detailed example:

```yaml
enabled: true
mode: socket

channels:
  - channel_name: "ops-assist"
    include_threads: true
  - channel_id: "C99999999"
    include_threads: false

behavior:
  requires_mention: true
  allow_dms: true
  prefixes:
    - "!lw"
    - "/assist"

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

Detailed example:

```yaml
enabled: true
poll_interval_seconds: 15

allowed_chats:
  - chat_id: "100100100"
    route_key: exec_updates
  - chat_id: "200200200"
    route_key: support_queue

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
description: Researches a topic and returns structured findings.
provider: claude_managed_agents
model: claude-sonnet-4-6

skills:
  - structured_notes

tools:
  built_in:
    - web_search
  mcp:
    external_research:
      allow:
        - search_documents
        - fetch_document
```

Detailed example:

```yaml
agent_id: researcher
enabled: true
display_name: Research Analyst
description: Collects evidence, compares sources, and prepares a downstream handoff.
provider: claude_managed_agents
model: claude-sonnet-4-6

memory:
  access: read_write

skills:
  - evidence_handbook
  - concise_handoffs

tools:
  built_in:
    - web_search
    - web_fetch
  mcp:
    catalyst_docs:
      allow:
        - search_articles
        - fetch_article
```

Fields:

- `agent_id`
- `enabled`
- `display_name`
- `description`
- `provider`
- `model`
- `skills`
- `tools.built_in`
- `tools.mcp`

## `workspace/agents/<agent_id>/AGENT.md`

Purpose:
- provide the stable system prompt for the agent

Required convention:

- describe the durable role of the agent
- explain what output shape the next step needs
- keep provider-specific filesystem details out of the prompt contract
- allow natural language output unless a structured format is explicitly required
- allow natural language output
- do not require JSON output

Sanitized example:

```md
You are the Triage agent.

Your job is to inspect inbound requests, identify the real task, and hand off clear next-step context.

Focus on:
- the user request
- missing constraints
- what the next agent needs to know

Return a concise handoff in natural language.
```

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

Sanitized example:

```md
---
name: evidence-handbook
description: Use when the task requires claims to be tied to cited evidence and uncertainty to be explicit.
---

Use this skill when the task requires evidence-backed synthesis.

Requirements:
- separate observations from conclusions
- note uncertainty explicitly
- keep the output easy for a downstream agent to reuse
```

Required frontmatter fields:

- `name`
- `description`

Expected behavior:

- `name` should be lowercase and hyphenated in the file
- LightWire converts that name into its internal skill key by replacing `-` with `_`
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
- `upstream_outputs_text`

Current convention:

- downstream prompts should primarily consume `upstream_outputs_text`
- prompts can still mention `payload.content` or `payload.summary` for human context if needed

Sanitized example:

```md
Summarize the incoming request and prepare a research plan.

User message:
{{ payload.text }}

If upstream work already exists, reuse it:
{{ upstream_outputs_text }}

Return:
- the main question
- the sub-questions to investigate
- the likely next agent needed
```
