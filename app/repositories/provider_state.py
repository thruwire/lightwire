from __future__ import annotations

from typing import Protocol

from app.models import ProviderStateRecord


class ProviderStateRepository(Protocol):
    def get(self, provider: str, resource_type: str, logical_key: str) -> ProviderStateRecord | None: ...

    def list_by_type(self, provider: str, resource_type: str) -> list[ProviderStateRecord]: ...

    def upsert(self, record: ProviderStateRecord) -> None: ...

    def delete(self, provider: str, resource_type: str, logical_key: str) -> None: ...
