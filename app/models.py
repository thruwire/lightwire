from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


class MessageSource(str, Enum):
    HEARTBEAT = "heartbeat"
    SLACK = "slack"
    TELEGRAM = "telegram"
    API = "api"
    AGENT_OUTPUT = "agent_output"


class MessageType(str, Enum):
    MESSAGE_CREATED = "message.created"
    HEARTBEAT_TICK = "heartbeat.tick"
    AGENT_COMPLETED = "agent.completed"


class NormalizedMessage(BaseModel):
    id: str
    source: MessageSource
    type: MessageType
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str
    parent_message_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RouteMatch(BaseModel):
    source: MessageSource
    type: MessageType
    agent_id: str | None = None


class RouteTarget(BaseModel):
    agent_id: str
    prompt_template: str


class RouteReplyConfig(BaseModel):
    connector: str
    mode: str


class RouteConfig(BaseModel):
    id: str
    enabled: bool = True
    match: RouteMatch
    target: RouteTarget
    reply: RouteReplyConfig | None = None
    require_artifacts: bool = False


class RoutesFile(BaseModel):
    routes: list[RouteConfig] = Field(default_factory=list)


class AgentMemoryConfig(BaseModel):
    access: str = "read_write"


class AgentMCPToolActivation(BaseModel):
    allow: list[str] = Field(default_factory=list)


class AgentToolsConfig(BaseModel):
    built_in: list[str] = Field(default_factory=list)
    mcp: dict[str, AgentMCPToolActivation] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    agent_id: str
    enabled: bool = True
    display_name: str | None = None
    description: str | None = None
    provider: str = "claude_managed_agents"
    model: str = "claude-sonnet-4-6"
    memory: AgentMemoryConfig
    skills: list[str] = Field(default_factory=list)
    tools: AgentToolsConfig = Field(default_factory=AgentToolsConfig)
    instruction_path: Path
    instructions: str
    config_path: Path


class SkillConfig(BaseModel):
    skill_id: str
    enabled: bool = True
    description: str | None = None
    instruction_path: Path
    instructions: str
    config_path: Path


class ToolPermissionPolicy(str, Enum):
    ALWAYS_ALLOW = "always_allow"
    ALWAYS_ASK = "always_ask"


class BuiltInToolConfig(BaseModel):
    enabled: bool = True
    permission_policy: ToolPermissionPolicy = ToolPermissionPolicy.ALWAYS_ALLOW


class MCPServerAuthType(str, Enum):
    NONE = "none"
    STATIC_BEARER_ENV = "static_bearer_env"
    MCP_OAUTH_ENV = "mcp_oauth_env"


class MCPServerAuthConfig(BaseModel):
    type: MCPServerAuthType = MCPServerAuthType.NONE
    token_env_var: str | None = None
    access_token_env_var: str | None = None
    refresh_token_env_var: str | None = None
    expires_at_env_var: str | None = None
    token_endpoint: str | None = None
    client_id_env_var: str | None = None
    client_secret_env_var: str | None = None
    token_endpoint_auth_method: str | None = None
    scope: str | None = None


class MCPServerConfig(BaseModel):
    enabled: bool = True
    type: str = "url"
    url: str
    auth: MCPServerAuthConfig = Field(default_factory=MCPServerAuthConfig)
    permission_policy: ToolPermissionPolicy = ToolPermissionPolicy.ALWAYS_ALLOW


class ToolsConfig(BaseModel):
    built_in: dict[str, BuiltInToolConfig] = Field(default_factory=dict)
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class HeartbeatTarget(BaseModel):
    agent_id: str
    prompt_template: str


class HeartbeatConfig(BaseModel):
    id: str
    enabled: bool = True
    interval_seconds: int
    target: HeartbeatTarget


class HeartbeatsFile(BaseModel):
    heartbeats: list[HeartbeatConfig] = Field(default_factory=list)


class SlackChannelConfig(BaseModel):
    channel_id: str | None = None
    channel_name: str | None = None
    include_threads: bool = True

    @model_validator(mode="after")
    def validate_target(self) -> "SlackChannelConfig":
        if self.channel_id or self.channel_name:
            return self
        raise ValueError("Slack channel config requires either channel_id or channel_name.")


class SlackBehaviorConfig(BaseModel):
    requires_mention: bool = True
    allow_dms: bool = True
    prefixes: list[str] = Field(default_factory=list)


class SlackMode(str, Enum):
    SOCKET = "socket"
    POLLING = "polling"
    WEBHOOK = "webhook"


class SlackSocketConfig(BaseModel):
    reconnect: bool = True
    ack_timeout_seconds: int = 3


class SlackPollingConfig(BaseModel):
    enabled: bool = False
    poll_interval_seconds: int = 30


class SlackConfig(BaseModel):
    enabled: bool = False
    mode: SlackMode = SlackMode.SOCKET
    socket: SlackSocketConfig = Field(default_factory=SlackSocketConfig)
    polling: SlackPollingConfig = Field(default_factory=SlackPollingConfig)
    channels: list[SlackChannelConfig] = Field(default_factory=list)
    behavior: SlackBehaviorConfig = Field(default_factory=SlackBehaviorConfig)
    ignore_bot_messages: bool = True
    send_replies: bool = True


class TelegramAllowedChatConfig(BaseModel):
    chat_id: str
    route_key: str = "default"


class TelegramConfig(BaseModel):
    enabled: bool = False
    poll_interval_seconds: int = 10
    allowed_chats: list[TelegramAllowedChatConfig] = Field(default_factory=list)
    ignore_bot_messages: bool = True
    send_replies: bool = True


class SessionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SessionRecord(BaseModel):
    id: str
    route_id: str | None = None
    agent_id: str
    message_id: str
    correlation_id: str
    prompt: str
    status: SessionStatus
    external_session_id: str | None = None
    memory_store_id: str
    output_message_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentOutputRecord(BaseModel):
    id: str
    session_id: str
    agent_id: str
    correlation_id: str
    content: str
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RoutedOutput(BaseModel):
    source_agent_id: str
    source_step_id: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class HeartbeatState(BaseModel):
    heartbeat_id: str
    next_run_at: datetime
    last_run_at: datetime | None = None


class ConnectorCursorRecord(BaseModel):
    connector: str
    scope: str
    cursor: str
    updated_at: datetime


class ProviderStateRecord(BaseModel):
    provider: str
    resource_type: str
    logical_key: str
    external_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class RouteDispatch(BaseModel):
    route_id: str
    agent_id: str
    prompt: str
    parent_message: NormalizedMessage
    reply: RouteReplyConfig | None = None
    require_artifacts: bool = False


class AppEventIn(BaseModel):
    source: MessageSource
    type: MessageType
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    parent_message_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClaudeSessionRequest(BaseModel):
    agent_id: str
    system_prompt: str
    task_prompt: str
    memory_store_id: str
    memory_access: str
    correlation_id: str
    vault_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ClaudeSessionResult(BaseModel):
    external_session_id: str
    status: SessionStatus
    content: str
    attached_memory_store_id: str
    raw: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str = "ok"
