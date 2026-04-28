from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.config import RuntimeConfig
from app.models import ConnectorCursorRecord, MessageSource, MessageType, NormalizedMessage
from app.repositories.cursors import ConnectorCursorRepository
from app.utils.ids import new_id
from app.utils.time import utc_now


DispatchCallback = Callable[[NormalizedMessage], Awaitable[object]]


class TelegramConnector:
    def __init__(
        self,
        config: RuntimeConfig,
        cursors: ConnectorCursorRepository,
        dispatch_message: DispatchCallback,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.cursors = cursors
        self.dispatch_message = dispatch_message
        self.client = client
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if not self.config.telegram.enabled or not self.config.settings.telegram_bot_token or self._task:
            return
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def poll_once(self) -> list[NormalizedMessage]:
        if not self.config.telegram.enabled or not self.config.settings.telegram_bot_token:
            return []
        cursor = self.cursors.get("telegram", "global")
        # Telegram exposes one monotonic update stream per bot, so a single global cursor is enough for v1.
        offset = int(cursor.cursor) + 1 if cursor else None
        updates = await self.fetch_updates(offset)
        normalized_messages: list[NormalizedMessage] = []
        for update in updates:
            update_id = int(update["update_id"])
            normalized = self.normalize_update(update)
            if normalized is not None:
                await self.dispatch_message(normalized)
                normalized_messages.append(normalized)
            # The cursor advances after each processed update, even when the update type is ignored.
            self._store_cursor(update_id)
        return normalized_messages

    async def fetch_updates(self, offset: int | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": 0}
        if offset is not None:
            params["offset"] = offset
        async with self._client() as client:
            response = await client.get(f"/bot{self.config.settings.telegram_bot_token}/getUpdates", params=params)
            response.raise_for_status()
            payload = response.json()
        return payload.get("result", [])

    def normalize_update(self, update: dict[str, Any]) -> NormalizedMessage | None:
        message = update.get("message")
        if not isinstance(message, dict):
            # v1 only handles plain incoming messages; edited messages and callback queries can come later.
            return None
        text = message.get("text")
        if not isinstance(text, str):
            return None
        sender = message.get("from") or {}
        if self.config.telegram.ignore_bot_messages and sender.get("is_bot"):
            return None
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        route_key = "default"
        if self.config.telegram.allowed_chats:
            chat_config = next((item for item in self.config.telegram.allowed_chats if item.chat_id == chat_id), None)
            if not chat_config:
                return None
            route_key = chat_config.route_key
        return NormalizedMessage(
            id=new_id("msg"),
            source=MessageSource.TELEGRAM,
            type=MessageType.MESSAGE_CREATED,
            payload={
                "chat_id": chat_id,
                "message_id": message.get("message_id"),
                "from_user_id": str(sender.get("id", "")),
                "from_username": sender.get("username"),
                "text": text,
                "date": message.get("date"),
            },
            correlation_id=new_id("corr"),
            parent_message_id=None,
            metadata={"connector": "telegram", "route_key": route_key},
            created_at=utc_now(),
        )

    async def send_message(self, chat_id: str, text: str, reply_to_message_id: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        async with self._client() as client:
            response = await client.post(f"/bot{self.config.settings.telegram_bot_token}/sendMessage", json=payload)
            response.raise_for_status()
            return response.json()

    async def _poll_loop(self) -> None:
        while True:
            await self.poll_once()
            await asyncio.sleep(self.config.telegram.poll_interval_seconds)

    def _store_cursor(self, update_id: int) -> None:
        self.cursors.upsert(
            ConnectorCursorRecord(
                connector="telegram",
                scope="global",
                cursor=str(update_id),
                updated_at=utc_now(),
            )
        )

    def _client(self) -> httpx.AsyncClient | _TelegramClientContext:
        if self.client:
            return _TelegramClientContext(self.client)
        return httpx.AsyncClient(base_url="https://api.telegram.org", timeout=30.0)


class _TelegramClientContext:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self.client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None
