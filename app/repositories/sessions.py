from __future__ import annotations

from typing import Protocol

from app.models import SessionRecord, SessionStatus


class SessionRepository(Protocol):
    def create(self, session: SessionRecord) -> None: ...

    def get(self, session_id: str) -> SessionRecord | None: ...

    def update_status(
        self,
        session_id: str,
        status: SessionStatus,
        external_session_id: str | None = None,
        output_message_id: str | None = None,
    ) -> None: ...
