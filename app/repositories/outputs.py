from __future__ import annotations

from typing import Protocol

from app.models import AgentOutputRecord


class AgentOutputRepository(Protocol):
    def create(self, output: AgentOutputRecord) -> None: ...

    def get_by_session(self, session_id: str) -> AgentOutputRecord | None: ...
