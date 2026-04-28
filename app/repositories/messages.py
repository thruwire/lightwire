from __future__ import annotations

from typing import Protocol

from app.models import NormalizedMessage


class MessageRepository(Protocol):
    def create(self, message: NormalizedMessage) -> None: ...

    def get(self, message_id: str) -> NormalizedMessage | None: ...
