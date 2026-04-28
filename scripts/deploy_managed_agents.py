from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import build_state


async def main() -> None:
    state = build_state()
    await state.resources.ensure()
    await state.resources.ensure_all_agent_vaults()
    results = []
    for agent_id, agent in state.config.agents.items():
        if not agent.enabled:
            continue
        # Deployment reuses the same workspace config as runtime so drift between deploy and execute stays low.
        results.append(await state.claude.create_or_update_agent(agent_id))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
