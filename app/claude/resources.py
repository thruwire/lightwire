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

    async def ensure(self, *, verify_remote: bool = False) -> tuple[str, str]:
        # Provider IDs are cached in SQLite so deploy/startup can be safely repeated.
        environment = self.repository.get("claude_managed_agents", "environment", "default")
        memory_store = self.repository.get("claude_managed_agents", "memory_store", "shared")

        if environment and memory_store and not verify_remote:
            self.config.attach_provider_state(environment.external_id, memory_store.external_id)
            return environment.external_id, memory_store.external_id

        if environment and memory_store and verify_remote:
            environment_exists, memory_store_exists = await self._verify_cached_core_resources(
                environment.external_id,
                memory_store.external_id,
            )
            if environment_exists and memory_store_exists:
                self.config.attach_provider_state(environment.external_id, memory_store.external_id)
                return environment.external_id, memory_store.external_id

        environment_id = await self.client.create_environment(self._environment_name())
        environment = ProviderStateRecord(
            provider="claude_managed_agents",
            resource_type="environment",
            logical_key="default",
            external_id=environment_id,
            metadata={"workspace": self.config.workspace_path.name},
            created_at=environment.created_at if environment else utc_now(),
            updated_at=utc_now(),
        )
        self.repository.upsert(environment)

        memory_store_id = await self.client.create_memory_store(self._memory_store_name())
        memory_store = ProviderStateRecord(
            provider="claude_managed_agents",
            resource_type="memory_store",
            logical_key="shared",
            external_id=memory_store_id,
            metadata={"workspace": self.config.workspace_path.name, "access": "read_write"},
            created_at=memory_store.created_at if memory_store else utc_now(),
            updated_at=utc_now(),
        )
        self.repository.upsert(memory_store)

        self.config.attach_provider_state(environment.external_id, memory_store.external_id)
        return environment.external_id, memory_store.external_id

    def attach_runtime_provider_state(self) -> tuple[str, str]:
        """Load previously deployed provider IDs without mutating remote state.

        Runtime startup should be deterministic and side-effect free. If the deploy
        step has not persisted the required IDs yet, startup fails fast instead of
        attempting an implicit provider mutation.
        """

        environment = self.repository.get("claude_managed_agents", "environment", "default")
        memory_store = self.repository.get("claude_managed_agents", "memory_store", "shared")
        if not environment or not memory_store:
            raise RuntimeError(
                "Provider resources have not been deployed yet. Run the managed-agent deploy step "
                "before starting ThruFlow in live mode."
            )
        self.config.attach_provider_state(environment.external_id, memory_store.external_id)
        return environment.external_id, memory_store.external_id

    async def ensure_agent_vaults(self, agent_id: str, *, verify_remote: bool = False) -> list[str]:
        agent = self.config.get_agent(agent_id)
        if not agent.tools.mcp:
            return []

        vault_key = f"vault:{agent_id}"
        # Vaults are per-agent because the same MCP server may be enabled for some agents and withheld from others.
        vault = self.repository.get("claude_managed_agents", "vault", vault_key)
        if vault and not verify_remote:
            vault_id = vault.external_id
        elif vault and verify_remote and await self.client.vault_exists(vault.external_id):
            vault_id = vault.external_id
        else:
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
                created_at=vault.created_at if vault else utc_now(),
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
            if credential and not verify_remote:
                continue
            if credential and verify_remote and await self.client.vault_credential_exists(vault_id, credential.external_id):
                continue
            credential_id = await self.client.create_or_update_vault_credential(
                vault_id=vault_id,
                display_name=f"{agent.display_name or agent.agent_id} {server_name} Credential",
                metadata={"workspace": self.config.workspace_path.name, "agent_id": agent.agent_id, "server_name": server_name},
                auth=auth_payload,
            )
            credential = ProviderStateRecord(
                provider="claude_managed_agents",
                resource_type="vault_credential",
                logical_key=credential_key,
                external_id=credential_id,
                metadata={"vault_id": vault_id, "server_name": server_name},
                created_at=credential.created_at if credential else utc_now(),
                updated_at=utc_now(),
            )
            self.repository.upsert(credential)
        return [vault_id]

    async def ensure_all_agent_vaults(self, *, verify_remote: bool = False) -> None:
        for agent_id, agent in self.config.agents.items():
            if not agent.enabled:
                continue
            await self.ensure_agent_vaults(agent_id, verify_remote=verify_remote)

    def assert_runtime_ready(self) -> None:
        """Validate that the deploy step has produced all required provider state."""

        self.attach_runtime_provider_state()
        for agent_id, agent in self.config.agents.items():
            if not agent.enabled:
                continue
            agent_record = self.repository.get("claude_managed_agents", "agent", agent_id)
            if not agent_record:
                raise RuntimeError(
                    f"Agent '{agent_id}' has not been deployed yet. Run the managed-agent deploy step "
                    "before starting ThruFlow in live mode."
                )
            if not agent.tools.mcp:
                continue
            vault_record = self.repository.get("claude_managed_agents", "vault", f"vault:{agent_id}")
            if not vault_record:
                raise RuntimeError(
                    f"Vault for agent '{agent_id}' has not been deployed yet. Run the managed-agent deploy step "
                    "before starting ThruFlow in live mode."
                )
            for server_name in sorted(agent.tools.mcp):
                server = self.config.tools.mcp_servers.get(server_name)
                if not server or not server.enabled or server.auth.type == MCPServerAuthType.NONE:
                    continue
                credential = self.repository.get(
                    "claude_managed_agents",
                    "vault_credential",
                    f"vault_credential:{agent_id}:{server_name}",
                )
                if not credential:
                    raise RuntimeError(
                        f"Vault credential for agent '{agent_id}' and MCP server '{server_name}' has not been deployed yet. "
                        "Run the managed-agent deploy step before starting ThruFlow in live mode."
                    )

    async def _verify_cached_core_resources(self, environment_id: str, memory_store_id: str) -> tuple[bool, bool]:
        return (
            await self.client.environment_exists(environment_id),
            await self.client.memory_store_exists(memory_store_id),
        )

    def resolve_agent_provider_id(self, agent_id: str) -> str:
        if self.config.settings.thruflow_fake_claude:
            return agent_id
        record = self.repository.get("claude_managed_agents", "agent", agent_id)
        if not record:
            raise RuntimeError(
                f"Agent '{agent_id}' has not been deployed yet. Run the managed-agent deploy step so "
                "ThruFlow can persist the provider agent ID before starting live sessions."
            )
        return record.external_id

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
