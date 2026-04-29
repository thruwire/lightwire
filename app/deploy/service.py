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
        return results
