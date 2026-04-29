from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import build_state
from app.models import ProviderStateRecord
from app.utils.time import utc_now


async def main() -> None:
    state = build_state()
    await state.resources.ensure(verify_remote=True)
    await state.resources.ensure_all_agent_vaults(verify_remote=True)
    results = []
    for agent_id, agent in state.config.agents.items():
        if not agent.enabled:
            continue
        # Deployment reuses the same workspace config as runtime so drift between deploy and execute stays low.
        result = await state.claude.create_or_update_agent(agent_id)
        state.provider_state.upsert(
            ProviderStateRecord(
                provider="claude_managed_agents",
                resource_type="agent",
                logical_key=agent_id,
                external_id=str(result["id"]),
                metadata={"name": result.get("name"), "version": result.get("version")},
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        results.append(result)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
