from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.claude.client import ClaudeManagedAgentClient
from app.config import RuntimeConfig
from app.models import MCPServerAuthType, ProviderStateRecord, SkillConfig
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
                "before starting LightWire in live mode."
            )
        self.config.attach_provider_state(environment.external_id, memory_store.external_id)
        return environment.external_id, memory_store.external_id

    def resolve_agent_vault_ids(self, agent_id: str) -> list[str]:
        """Return previously deployed vault IDs for runtime session creation.

        Managed Agents sessions should reference vaults that were provisioned during
        the explicit deploy step. Runtime dispatch must stay read-only and avoid
        reminting or rewriting vault credentials on live traffic.
        """

        server_names = self._enabled_authenticated_mcp_servers(agent_id)
        if not server_names:
            return []

        vault_record = self.repository.get("claude_managed_agents", "vault", self._shared_vault_key())
        if not vault_record:
            raise RuntimeError(
                "Shared MCP vault has not been deployed yet. Run the managed-agent deploy step "
                "before starting LightWire in live mode."
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
                    "Run the managed-agent deploy step before starting LightWire in live mode."
                )
        return [vault_record.external_id]

    async def ensure_agent_vaults(self, agent_id: str, *, verify_remote: bool = False) -> list[str]:
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

    async def ensure_skills(self, *, verify_remote: bool = False) -> None:
        for skill_id in self._enabled_skill_ids():
            await self.ensure_skill(skill_id, verify_remote=verify_remote)

    async def ensure_skill(self, skill_id: str, *, verify_remote: bool = False) -> str:
        skill = self.config.skills[skill_id]
        record = self.repository.get("claude_managed_agents", "skill", skill_id)
        content_hash = self._compute_skill_hash(skill)
        display_title = self._skill_display_title(skill)
        skill_dir = skill.instruction_path.parent

        remote_skill_id = ""
        remote_exists = False
        if record:
            remote_skill_id = record.external_id
            if not verify_remote:
                remote_exists = True
            elif await self.client.skill_exists(remote_skill_id):
                remote_exists = True

        if remote_exists and str(record.metadata.get("content_hash") or "") == content_hash:
            return remote_skill_id

        created_at = record.created_at if record else utc_now()
        latest_version = ""
        if not remote_exists:
            existing = await self.client.find_custom_skill_by_display_title(display_title)
            if existing:
                remote_skill_id = self.client._extract_id(existing)
                remote_exists = True

        if remote_exists:
            version = await self.client.create_skill_version(remote_skill_id, skill_dir)
            latest_version = str(version.get("version") or "latest")
        else:
            created = await self.client.create_skill(display_title, skill_dir)
            remote_skill_id = str(created["id"])
            latest_version = str(created.get("latest_version") or "latest")

        self.repository.upsert(
            ProviderStateRecord(
                provider="claude_managed_agents",
                resource_type="skill",
                logical_key=skill_id,
                external_id=remote_skill_id,
                metadata={
                    "workspace_id": self.config.get_workspace_id(),
                    "provider_name": skill.provider_name,
                    "display_title": display_title,
                    "content_hash": content_hash,
                    "latest_version": latest_version,
                },
                created_at=created_at,
                updated_at=utc_now(),
            )
        )
        return remote_skill_id

    def resolve_agent_custom_skills(self, agent_id: str) -> list[dict[str, str]]:
        custom_skills: list[dict[str, str]] = []
        for skill_id in self.config.get_agent(agent_id).skills:
            skill = self.config.skills.get(skill_id)
            if not skill or not skill.enabled:
                continue
            record = self.repository.get("claude_managed_agents", "skill", skill_id)
            if not record:
                raise RuntimeError(
                    f"Custom skill '{skill_id}' has not been deployed yet. Run the managed-agent deploy step "
                    "before starting LightWire in live mode."
                )
            custom_skills.append({"type": "custom", "skill_id": record.external_id, "version": "latest"})
        return custom_skills

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
                    "before starting LightWire in live mode."
                )
            server_names = self._enabled_authenticated_mcp_servers(agent_id)
            if not server_names:
                continue
            vault_record = self.repository.get("claude_managed_agents", "vault", self._shared_vault_key())
            if not vault_record:
                raise RuntimeError(
                    "Shared MCP vault has not been deployed yet. Run the managed-agent deploy step "
                    "before starting LightWire in live mode."
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
                        "Run the managed-agent deploy step before starting LightWire in live mode."
                    )

    async def _verify_cached_core_resources(self, environment_id: str, memory_store_id: str) -> tuple[bool, bool]:
        environment = await self._retrieve_matching_environment(environment_id)
        memory_store = await self._retrieve_matching_memory_store(memory_store_id)
        return environment is not None, memory_store is not None

    async def _retrieve_matching_environment(self, environment_id: str) -> dict[str, object] | None:
        try:
            payload = await self.client.retrieve_environment(environment_id)
        except RuntimeError:
            return None
        if payload.get("archived_at"):
            return None
        if str(payload.get("name") or "") != self._environment_name():
            return None
        metadata = payload.get("metadata") or {}
        if str(metadata.get("workspace_id") or "") != self.config.get_workspace_id():
            return None
        return payload

    async def _retrieve_matching_memory_store(self, memory_store_id: str) -> dict[str, object] | None:
        try:
            payload = await self.client.retrieve_memory_store(memory_store_id)
        except RuntimeError:
            return None
        if str(payload.get("name") or "") != self._memory_store_name():
            return None
        metadata = payload.get("metadata") or {}
        if str(metadata.get("workspace_id") or "") != self.config.get_workspace_id():
            return None
        return payload

    def resolve_agent_provider_id(self, agent_id: str) -> str:
        if self.config.settings.lightwire_fake_claude:
            return agent_id
        record = self.repository.get("claude_managed_agents", "agent", agent_id)
        if not record:
            raise RuntimeError(
                f"Agent '{agent_id}' has not been deployed yet. Run the managed-agent deploy step so "
                "LightWire can persist the provider agent ID before starting live sessions."
            )
        return record.external_id

    def _environment_name(self) -> str:
        return f"lightwire-{self.config.get_workspace_id()}-environment"

    def _memory_store_name(self) -> str:
        return f"lightwire-{self.config.get_workspace_id()}-shared-memory"

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

        if self.config.settings.lightwire_fake_claude:
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

    def _enabled_skill_ids(self) -> list[str]:
        enabled: set[str] = set()
        for agent_id, agent in self.config.agents.items():
            if not agent.enabled:
                continue
            for skill_id in agent.skills:
                skill = self.config.skills.get(skill_id)
                if skill and skill.enabled:
                    enabled.add(skill_id)
        return sorted(enabled)

    def _skill_display_title(self, skill: SkillConfig) -> str:
        return f"{self.config.get_workspace_id()} / {skill.provider_name}"

    def _skill_file_paths(self, skill: SkillConfig) -> list[Path]:
        return sorted(path for path in skill.instruction_path.parent.rglob("*") if path.is_file())

    def _compute_skill_hash(self, skill: SkillConfig) -> str:
        digest = hashlib.sha256()
        for path in self._skill_file_paths(skill):
            digest.update(path.relative_to(skill.instruction_path.parent).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def _require_auth_value(self, value: str | None, field_name: str, server_name: str) -> str:
        if value:
            return value
        raise ValueError(f"MCP server '{server_name}' requires auth field '{field_name}' to be configured.")

    def _require_env(self, env_var: str | None, server_name: str) -> str:
        if not env_var:
            raise ValueError(f"MCP server '{server_name}' is missing an environment variable reference in its auth config.")
        value = os.environ.get(env_var, "")
        if not value and self.config.settings.lightwire_fake_claude:
            return f"mock-{env_var.lower()}"
        if not value:
            raise ValueError(f"MCP server '{server_name}' requires environment variable '{env_var}' to be set.")
        return value
