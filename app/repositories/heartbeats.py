from __future__ import annotations

from typing import Protocol

from app.models import HeartbeatState


class HeartbeatRepository(Protocol):
    def upsert(self, state: HeartbeatState) -> None: ...

    def get(self, heartbeat_id: str) -> HeartbeatState | None: ...

    def list_all(self) -> list[HeartbeatState]: ...
