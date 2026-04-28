from __future__ import annotations

from typing import Protocol

from app.models import ConnectorCursorRecord


class ConnectorCursorRepository(Protocol):
    def get(self, connector: str, scope: str) -> ConnectorCursorRecord | None: ...

    def upsert(self, record: ConnectorCursorRecord) -> None: ...
