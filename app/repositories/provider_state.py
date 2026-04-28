from __future__ import annotations

from typing import Protocol

from app.models import ProviderStateRecord


class ProviderStateRepository(Protocol):
    def get(self, provider: str, resource_type: str, logical_key: str) -> ProviderStateRecord | None: ...

    def upsert(self, record: ProviderStateRecord) -> None: ...
