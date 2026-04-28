from __future__ import annotations

from typing import Any


class ClaudeMemoryStoreClient:
    async def read(self, path: str) -> dict[str, Any]:
        # The repo currently treats memory operations as provider-facing stubs that can be expanded later.
        return {"path": path, "content": None}

    async def write(self, path: str, content: str) -> dict[str, Any]:
        return {"path": path, "content": content}

    async def delete(self, path: str) -> dict[str, Any]:
        return {"path": path, "deleted": True}
