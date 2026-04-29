from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import build_state


async def main() -> None:
    state = build_state()
    # The script is an operational wrapper only; deployment behavior lives in app code.
    results = await state.deploy.apply()
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
