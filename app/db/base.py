from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class Database(Protocol):
    def initialize(self) -> None: ...

    def execute(self, query: str, params: Sequence[Any] = ()) -> None: ...

    def fetchone(self, query: str, params: Sequence[Any] = ()) -> dict[str, Any] | None: ...

    def fetchall(self, query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]: ...
