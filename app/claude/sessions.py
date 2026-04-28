from __future__ import annotations

from app.claude.client import ClaudeManagedAgentClient
from app.models import ClaudeSessionRequest, ClaudeSessionResult


class ClaudeSessionService:
    def __init__(self, client: ClaudeManagedAgentClient) -> None:
        self.client = client

    async def run(self, request: ClaudeSessionRequest) -> ClaudeSessionResult:
        # This small wrapper keeps the runner isolated from the provider client shape.
        return await self.client.create_session(request)
