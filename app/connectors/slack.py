from __future__ import annotations

from typing import Any

import httpx

from app.config import RuntimeConfig
from app.models import ConnectorCursorRecord, MessageSource, MessageType, NormalizedMessage
from app.repositories.cursors import ConnectorCursorRepository
from app.utils.ids import new_id
from app.utils.time import utc_now


class SlackPoller:
    def __init__(
        self,
        config: RuntimeConfig,
        cursors: ConnectorCursorRepository,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.cursors = cursors
        self.client = client

    async def poll(self) -> list[NormalizedMessage]:
        if not self.config.slack.enabled or not self.config.settings.slack_bot_token:
            return []
        messages: list[NormalizedMessage] = []
        async with self._client() as client:
            for channel in self.config.slack.channels:
                # Each channel and thread keeps its own cursor so replays stay narrow after restarts.
                messages.extend(await self._poll_channel(client, channel.channel_id, channel.include_threads))
        return messages

    async def _poll_channel(self, client: httpx.AsyncClient, channel_id: str, include_threads: bool) -> list[NormalizedMessage]:
        scope = f"channel:{channel_id}"
        cursor = self.cursors.get("slack", scope)
        params: dict[str, Any] = {"channel": channel_id, "oldest": cursor.cursor if cursor else "0"}
        response = await client.get("/conversations.history", params=params)
        response.raise_for_status()
        items = response.json().get("messages", [])
        normalized = self._to_messages(channel_id, items, None)
        if include_threads:
            for item in items:
                thread_ts = item.get("thread_ts")
                if thread_ts and thread_ts == item.get("ts"):
                    # Slack only needs thread polling for root thread messages; replies hang off the same thread_ts.
                    normalized.extend(await self._poll_thread(client, channel_id, thread_ts))
        latest_ts = max((item.get("ts", "0") for item in items), default=cursor.cursor if cursor else "0")
        self.cursors.upsert(
            ConnectorCursorRecord(
                connector="slack",
                scope=scope,
                cursor=latest_ts,
                updated_at=utc_now(),
            )
        )
        return normalized

    async def _poll_thread(self, client: httpx.AsyncClient, channel_id: str, thread_ts: str) -> list[NormalizedMessage]:
        scope = f"thread:{channel_id}:{thread_ts}"
        cursor = self.cursors.get("slack", scope)
        response = await client.get(
            "/conversations.replies",
            params={"channel": channel_id, "ts": thread_ts, "oldest": cursor.cursor if cursor else "0"},
        )
        response.raise_for_status()
        items = response.json().get("messages", [])
        latest_ts = max((item.get("ts", "0") for item in items), default=cursor.cursor if cursor else "0")
        self.cursors.upsert(
            ConnectorCursorRecord(
                connector="slack",
                scope=scope,
                cursor=latest_ts,
                updated_at=utc_now(),
            )
        )
        return self._to_messages(channel_id, items, thread_ts)

    def _to_messages(
        self,
        channel_id: str,
        items: list[dict[str, Any]],
        thread_ts: str | None,
    ) -> list[NormalizedMessage]:
        normalized: list[NormalizedMessage] = []
        for item in items:
            if self.config.slack.ignore_bot_messages and item.get("bot_id"):
                continue
            normalized.append(
                NormalizedMessage(
                    id=new_id("msg"),
                    source=MessageSource.SLACK,
                    type=MessageType.MESSAGE_CREATED,
                    payload={
                        "text": item.get("text", ""),
                        "channel_id": channel_id,
                        "thread_ts": thread_ts or item.get("thread_ts"),
                        "slack_ts": item.get("ts"),
                        "user": item.get("user"),
                    },
                    correlation_id=new_id("corr"),
                    parent_message_id=None,
                    metadata={"channel_id": channel_id},
                    created_at=utc_now(),
                )
            )
        return normalized

    def _client(self) -> httpx.AsyncClient | _SlackClientContext:
        if self.client:
            return _SlackClientContext(self.client)
        return httpx.AsyncClient(
            base_url="https://slack.com/api",
            headers={"Authorization": f"Bearer {self.config.settings.slack_bot_token}"},
            timeout=30.0,
        )


class _SlackClientContext:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self.client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None
