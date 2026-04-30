from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import httpx

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
            metadata={"workspace_id": self.config.get_workspace_id()},
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
            metadata={"workspace_id": self.config.get_workspace_id(), "access": "read_write"},
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
        server_names = self._enabled_authenticated_mcp_servers(agent_id)
        if not server_names:
            return []

        vault_key = self._shared_vault_key()
        vault = self.repository.get("claude_managed_agents", "vault", vault_key)
        if vault and not verify_remote:
            vault_id = vault.external_id
        elif vault and verify_remote and await self.client.vault_exists(vault.external_id):
            vault_id = vault.external_id
        else:
            vault_id = await self.client.create_vault(
                display_name=f"{self.config.get_workspace_id()} Shared MCP",
                metadata={
                    "workspace_id": self.config.get_workspace_id(),
                    "vault_scope": "shared_mcp",
                    "managed_agents_slug": f"{self.config.get_workspace_id()}:shared-mcp",
                },
            )
            vault = ProviderStateRecord(
                provider="claude_managed_agents",
                resource_type="vault",
                logical_key=vault_key,
                external_id=vault_id,
                metadata={"workspace_id": self.config.get_workspace_id(), "vault_scope": "shared_mcp"},
                created_at=vault.created_at if vault else utc_now(),
                updated_at=utc_now(),
            )
            self.repository.upsert(vault)

        for server_name in server_names:
            credential_key = self._shared_credential_key(server_name)
            credential = self.repository.get("claude_managed_agents", "vault_credential", credential_key)
            auth_payload = await self._build_vault_auth_payload(server_name)
            if credential and not verify_remote:
                continue
            credential_id = await self.client.create_or_update_vault_credential(
                vault_id=vault_id,
                display_name=f"{server_name} Shared Credential",
                metadata={
                    "workspace_id": self.config.get_workspace_id(),
                    "server_name": server_name,
                    "vault_scope": "shared_mcp",
                    "managed_agents_slug": f"{self.config.get_workspace_id()}:shared-mcp:{server_name}",
                },
                auth=auth_payload,
            )
            credential = ProviderStateRecord(
                provider="claude_managed_agents",
                resource_type="vault_credential",
                logical_key=credential_key,
                external_id=credential_id,
                metadata={"vault_id": vault_id, "server_name": server_name, "vault_scope": "shared_mcp"},
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
            server_names = self._enabled_authenticated_mcp_servers(agent_id)
            if not server_names:
                continue
            vault_record = self.repository.get("claude_managed_agents", "vault", self._shared_vault_key())
            if not vault_record:
                raise RuntimeError(
                    "Shared MCP vault has not been deployed yet. Run the managed-agent deploy step "
                    "before starting ThruFlow in live mode."
                )
            for server_name in server_names:
                credential = self.repository.get(
                    "claude_managed_agents",
                    "vault_credential",
                    self._shared_credential_key(server_name),
                )
                if not credential:
                    raise RuntimeError(
                        f"Shared vault credential for MCP server '{server_name}' has not been deployed yet. "
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
        return f"thruflow-{self.config.get_workspace_id()}-environment"

    def _memory_store_name(self) -> str:
        return f"thruflow-{self.config.get_workspace_id()}-shared-memory"

    async def _build_vault_auth_payload(self, server_name: str) -> dict[str, object]:
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
            # Backward-compatible path: token material is sourced directly from env.
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
        if auth.type == MCPServerAuthType.MCP_OAUTH_CLIENT_CREDENTIALS_ENV:
            minted = await self._mint_client_credentials_token_pair(server_name)
            refresh: dict[str, object] = {
                "client_id": self._require_env(auth.client_id_env_var, server_name),
                "refresh_token": minted["refresh_token"],
                "token_endpoint": self._require_auth_value(auth.token_endpoint, "token_endpoint", server_name),
                "token_endpoint_auth": {
                    "type": auth.token_endpoint_auth_method or "client_secret_post",
                },
            }
            scope = auth.scope or minted.get("scope") or ""
            if scope:
                refresh["scope"] = scope
            auth_method = auth.token_endpoint_auth_method or "client_secret_post"
            if auth_method != "none":
                refresh["token_endpoint_auth"]["client_secret"] = self._require_env(auth.client_secret_env_var, server_name)
            return {
                "type": "mcp_oauth",
                "mcp_server_url": server.url,
                "access_token": minted["access_token"],
                "expires_at": minted["expires_at"],
                "refresh": refresh,
            }
        raise ValueError(f"Unsupported auth type for MCP server '{server_name}': {auth.type}")

    async def _mint_client_credentials_token_pair(self, server_name: str) -> dict[str, str]:
        server = self.config.tools.mcp_servers[server_name]
        auth = server.auth
        token_endpoint = self._require_auth_value(auth.token_endpoint, "token_endpoint", server_name)
        client_id = self._require_env(auth.client_id_env_var, server_name)
        auth_method = auth.token_endpoint_auth_method or "client_secret_post"
        client_secret = ""
        if auth_method != "none":
            client_secret = self._require_env(auth.client_secret_env_var, server_name)

        if self.config.settings.thruflow_fake_claude:
            return {
                "access_token": f"mock-{server_name}-access-token",
                "refresh_token": f"mock-{server_name}-refresh-token",
                "expires_at": "2099-01-01T00:00:00Z",
                "scope": auth.scope or "",
            }

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {"grant_type": "client_credentials"}
        if auth_method == "client_secret_basic":
            headers["Authorization"] = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
        elif auth_method == "client_secret_post":
            data["client_id"] = client_id
            data["client_secret"] = client_secret
        elif auth_method == "none":
            data["client_id"] = client_id
        else:
            raise ValueError(
                f"MCP server '{server_name}' has unsupported token_endpoint_auth_method '{auth_method}'. "
                "Expected one of: none, client_secret_basic, client_secret_post."
            )

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(token_endpoint, data=data, headers=headers)
        response.raise_for_status()
        payload = response.json()

        access_token = str(payload.get("access_token") or "")
        refresh_token = str(payload.get("refresh_token") or "")
        expires_in = payload.get("expires_in")
        if not access_token or not refresh_token or expires_in in (None, ""):
            raise ValueError(
                f"MCP server '{server_name}' client-credentials bootstrap requires token endpoint "
                "responses containing access_token, refresh_token, and expires_in so Anthropic can refresh the credential."
            )

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "scope": str(payload.get("scope") or ""),
        }

    def _enabled_authenticated_mcp_servers(self, agent_id: str) -> list[str]:
        agent = self.config.get_agent(agent_id)
        server_names: list[str] = []
        for server_name in sorted(agent.tools.mcp):
            server = self.config.tools.mcp_servers.get(server_name)
            if not server or not server.enabled or server.auth.type == MCPServerAuthType.NONE:
                continue
            server_names.append(server_name)
        return server_names

    def _shared_vault_key(self) -> str:
        return "shared"

    def _shared_credential_key(self, server_name: str) -> str:
        return f"shared:{server_name}"

    def _require_auth_value(self, value: str | None, field_name: str, server_name: str) -> str:
        if value:
            return value
        raise ValueError(f"MCP server '{server_name}' requires auth field '{field_name}' to be configured.")

    def _require_env(self, env_var: str | None, server_name: str) -> str:
        if not env_var:
            raise ValueError(f"MCP server '{server_name}' is missing an environment variable reference in its auth config.")
        value = os.environ.get(env_var, "")
        if not value and self.config.settings.thruflow_fake_claude:
            return f"mock-{env_var.lower()}"
        if not value:
            raise ValueError(f"MCP server '{server_name}' requires environment variable '{env_var}' to be set.")
        return value
