from __future__ import annotations

from app.claude.resources import ClaudeProviderResourceService
from app.config import RuntimeConfig
from app.models import ProviderStateRecord
from app.repositories.provider_state import ProviderStateRepository
from app.utils.time import utc_now


class DeploymentService:
    """Apply the workspace to the provider and persist the resulting provider IDs.

    Runtime startup should not mutate provider resources. This service is the explicit
    control-plane entrypoint for create/update/repair operations instead.
    """

    def __init__(
        self,
        config: RuntimeConfig,
        resources: ClaudeProviderResourceService,
        provider_state: ProviderStateRepository,
    ) -> None:
        self.config = config
        self.resources = resources
        self.provider_state = provider_state

    async def apply(self) -> list[dict[str, object]]:
        # Deploy verifies cached IDs remotely so stale local state can be repaired
        # after an operator manually deletes provider-side resources.
        await self.resources.ensure(verify_remote=True)
        await self.resources.ensure_all_agent_vaults(verify_remote=True)
        results: list[dict[str, object]] = []
        for agent_id, agent in self.config.agents.items():
            if not agent.enabled:
                continue
            # Agent deploy prefers the cached provider ID when available so repeated
            # deploys update the same remote agent instead of depending on list lookup.
            existing = self.provider_state.get("claude_managed_agents", "agent", agent_id)
            result = await self.resources.client.deploy_agent(
                self.config.get_agent(agent_id),
                self.config.get_agent_system_prompt(agent_id),
                existing_agent_id=existing.external_id if existing else None,
            )
            self.provider_state.upsert(
                ProviderStateRecord(
                    provider="claude_managed_agents",
                    resource_type="agent",
                    logical_key=agent_id,
                    external_id=str(result["id"]),
                    metadata={"name": result.get("name"), "version": result.get("version")},
                    created_at=existing.created_at if existing else utc_now(),
                    updated_at=utc_now(),
                )
            )
            results.append(result)
        await self._reconcile_removed_agents()
        return results

    async def _reconcile_removed_agents(self) -> None:
        configured_agent_ids = {agent_id for agent_id, agent in self.config.agents.items() if agent.enabled}
        configured_mcp_agents = {
            agent_id
            for agent_id, agent in self.config.agents.items()
            if agent.enabled and agent.tools.mcp
        }

        # SQLite reconciliation catches agents that used to exist locally but have
        # since been removed from workspace config.
        for record in self.provider_state.list_by_type("claude_managed_agents", "agent"):
            if record.logical_key in configured_agent_ids:
                continue
            await self.resources.client.archive_agent(record.external_id)
            self.provider_state.delete("claude_managed_agents", "agent", record.logical_key)

        for record in self.provider_state.list_by_type("claude_managed_agents", "vault_credential"):
            agent_id = record.logical_key.split(":")[1] if ":" in record.logical_key else ""
            if agent_id in configured_mcp_agents:
                continue
            self.provider_state.delete("claude_managed_agents", "vault_credential", record.logical_key)

        for record in self.provider_state.list_by_type("claude_managed_agents", "vault"):
            agent_id = record.logical_key.removeprefix("vault:")
            if agent_id in configured_mcp_agents:
                continue
            await self._archive_remote_vault_tree(record.external_id)
            self.provider_state.delete("claude_managed_agents", "vault", record.logical_key)

        # Remote reconciliation catches orphaned Anthropic resources even if the
        # local SQLite state was lost or never recorded correctly.
        for item in await self.resources.client.list_managed_agents():
            slug = str((item.get("metadata") or {}).get("managed_agents_slug") or "")
            if not slug or slug in configured_agent_ids:
                continue
            await self.resources.client.archive_agent(self.resources.client._extract_id(item))

        for item in await self.resources.client.list_managed_vaults():
            slug = str((item.get("metadata") or {}).get("managed_agents_slug") or "")
            if not slug or slug in configured_mcp_agents:
                continue
            await self._archive_remote_vault_tree(self.resources.client._extract_id(item))

    async def _archive_remote_vault_tree(self, vault_id: str) -> None:
        for credential in await self.resources.client.list_vault_credentials(vault_id):
            await self.resources.client.archive_vault_credential(vault_id, self.resources.client._extract_id(credential))
        await self.resources.client.archive_vault(vault_id)
