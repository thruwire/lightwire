from __future__ import annotations

import os

from app.claude.client import ClaudeManagedAgentClient
from app.config import RuntimeConfig
from app.models import MCPServerAuthType, ProviderStateRecord
from app.repositories.provider_state import ProviderStateRepository
from app.utils.time import utc_now


class ClaudeProviderResourceService:
    def __init__(
        self,
        config: RuntimeConfig,
        repository: ProviderStateRepository,
        client: ClaudeManagedAgentClient,
    ) -> None:
        self.config = config
        self.repository = repository
        self.client = client

    async def ensure(self) -> tuple[str, str]:
        # Provider IDs are cached in SQLite so deploy/startup can be safely repeated.
        environment = self.repository.get("claude_managed_agents", "environment", "default")
        memory_store = self.repository.get("claude_managed_agents", "memory_store", "shared")

        if not environment:
            environment_id = await self.client.create_environment(self._environment_name())
            environment = ProviderStateRecord(
                provider="claude_managed_agents",
                resource_type="environment",
                logical_key="default",
                external_id=environment_id,
                metadata={"workspace": self.config.workspace_path.name},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            self.repository.upsert(environment)

        if not memory_store:
            memory_store_id = await self.client.create_memory_store(self._memory_store_name())
            memory_store = ProviderStateRecord(
                provider="claude_managed_agents",
                resource_type="memory_store",
                logical_key="shared",
                external_id=memory_store_id,
                metadata={"workspace": self.config.workspace_path.name, "access": "read_write"},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            self.repository.upsert(memory_store)

        self.config.attach_provider_state(environment.external_id, memory_store.external_id)
        return environment.external_id, memory_store.external_id

    async def ensure_agent_vaults(self, agent_id: str) -> list[str]:
        agent = self.config.get_agent(agent_id)
        if not agent.tools.mcp:
            return []

        vault_key = f"vault:{agent_id}"
        # Vaults are per-agent because the same MCP server may be enabled for some agents and withheld from others.
        vault = self.repository.get("claude_managed_agents", "vault", vault_key)
        if not vault:
            vault_id = await self.client.create_vault(
                display_name=f"{agent.display_name or agent.agent_id} MCP",
                metadata={"workspace": self.config.workspace_path.name, "agent_id": agent.agent_id},
            )
            vault = ProviderStateRecord(
                provider="claude_managed_agents",
                resource_type="vault",
                logical_key=vault_key,
                external_id=vault_id,
                metadata={"workspace": self.config.workspace_path.name, "agent_id": agent.agent_id},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            self.repository.upsert(vault)

        for server_name in sorted(agent.tools.mcp):
            server = self.config.tools.mcp_servers.get(server_name)
            if not server or not server.enabled or server.auth.type == MCPServerAuthType.NONE:
                continue
            credential_key = f"vault_credential:{agent_id}:{server_name}"
            credential = self.repository.get("claude_managed_agents", "vault_credential", credential_key)
            auth_payload = self._build_vault_auth_payload(server_name)
            if not credential:
                credential_id = await self.client.create_or_update_vault_credential(
                    vault_id=vault.external_id,
                    display_name=f"{agent.display_name or agent.agent_id} {server_name} Credential",
                    metadata={"workspace": self.config.workspace_path.name, "agent_id": agent.agent_id, "server_name": server_name},
                    auth=auth_payload,
                )
                credential = ProviderStateRecord(
                    provider="claude_managed_agents",
                    resource_type="vault_credential",
                    logical_key=credential_key,
                    external_id=credential_id,
                    metadata={"vault_id": vault.external_id, "server_name": server_name},
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                self.repository.upsert(credential)
        return [vault.external_id]

    async def ensure_all_agent_vaults(self) -> None:
        for agent_id, agent in self.config.agents.items():
            if not agent.enabled:
                continue
            await self.ensure_agent_vaults(agent_id)

    def _environment_name(self) -> str:
        return f"thruflow-{self.config.workspace_path.name}-environment"

    def _memory_store_name(self) -> str:
        return f"thruflow-{self.config.workspace_path.name}-shared-memory"

    def _build_vault_auth_payload(self, server_name: str) -> dict[str, object]:
        server = self.config.tools.mcp_servers[server_name]
        auth = server.auth
        if auth.type == MCPServerAuthType.STATIC_BEARER_ENV:
            # Static bearer auth is the simplest path for generic third-party HTTP MCP servers.
            token = self._require_env(auth.token_env_var, server_name)
            return {
                "type": "static_bearer",
                "mcp_server_url": server.url,
                "token": token,
            }
        if auth.type == MCPServerAuthType.MCP_OAUTH_ENV:
            # OAuth tokens are still sourced from env in this repo; the provider vault stores the credential copy.
            payload: dict[str, object] = {
                "type": "mcp_oauth",
                "mcp_server_url": server.url,
                "access_token": self._require_env(auth.access_token_env_var, server_name),
            }
            expires_at = os.environ.get(auth.expires_at_env_var or "", "")
            if expires_at:
                payload["expires_at"] = expires_at
            refresh_token = os.environ.get(auth.refresh_token_env_var or "", "")
            if refresh_token:
                payload["refresh"] = {
                    "client_id": self._require_env(auth.client_id_env_var, server_name),
                    "refresh_token": refresh_token,
                    "token_endpoint": auth.token_endpoint,
                    "scope": auth.scope,
                    "token_endpoint_auth": {
                        "type": auth.token_endpoint_auth_method or "client_secret_post",
                        "client_secret": self._require_env(auth.client_secret_env_var, server_name),
                    },
                }
            return payload
        raise ValueError(f"Unsupported auth type for MCP server '{server_name}': {auth.type}")

    def _require_env(self, env_var: str | None, server_name: str) -> str:
        if not env_var:
            raise ValueError(f"MCP server '{server_name}' is missing an environment variable reference in its auth config.")
        value = os.environ.get(env_var, "")
        if not value and self.config.settings.thruflow_fake_claude:
            return f"mock-{env_var.lower()}"
        if not value:
            raise ValueError(f"MCP server '{server_name}' requires environment variable '{env_var}' to be set.")
        return value
