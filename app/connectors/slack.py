from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from app.config import RuntimeConfig
from app.models import ConnectorCursorRecord, MessageSource, MessageType, NormalizedMessage, SlackMode
from app.repositories.cursors import ConnectorCursorRepository
from app.utils.ids import new_id
from app.utils.time import utc_now

try:
    from slack_sdk.web.async_client import AsyncWebClient
except ImportError:  # pragma: no cover - exercised through injected fakes in tests.
    AsyncWebClient = None

try:
    from slack_sdk.socket_mode.aiohttp import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse
except ImportError:  # pragma: no cover - exercised through injected fakes in tests.
    SocketModeClient = None
    SocketModeRequest = Any
    SocketModeResponse = None


logger = logging.getLogger(__name__)

DispatchCallback = Callable[[NormalizedMessage], Awaitable[object]]


class SlackConnector:
    def __init__(
        self,
        config: RuntimeConfig,
        cursors: ConnectorCursorRepository,
        dispatch_message: DispatchCallback,
        *,
        polling_client: httpx.AsyncClient | None = None,
        web_client: Any | None = None,
        socket_client: Any | None = None,
    ) -> None:
        self.config = config
        self.cursors = cursors
        self.dispatch_message = dispatch_message
        self.polling_client = polling_client
        self.web_client = web_client
        self.socket_client = socket_client
        self._polling_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[object]] = set()
        self._bot_user_id: str | None = None
        self._allowed_channel_ids: set[str] = set()
        self._channel_thread_policy: dict[str, bool] = {}
        self._channel_name_cache: dict[str, str] = {}

    async def start(self) -> None:
        if not self.config.slack.enabled:
            return
        if self._should_use_socket_mode():
            self._ensure_socket_tokens()
            await self._prepare_runtime_state()
            if self.socket_client is None:
                if SocketModeClient is None:
                    raise RuntimeError("Slack Socket Mode requires slack_sdk Socket Mode support and its aiohttp dependency.")
                self.socket_client = SocketModeClient(
                    app_token=self.config.settings.slack_app_token,
                    web_client=self.web_client,
                )
            self._register_socket_listener()
            await self.socket_client.connect()
            return
        if self._should_use_polling_mode() and self._polling_task is None:
            self._ensure_bot_token()
            await self._prepare_runtime_state()
            self._polling_task = asyncio.create_task(self._polling_loop())
            return
        if self.config.slack.mode == SlackMode.WEBHOOK:
            raise NotImplementedError("Slack webhook mode is not implemented.")

    async def stop(self) -> None:
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            finally:
                self._polling_task = None
        for task in list(self._background_tasks):
            task.cancel()
        for task in list(self._background_tasks):
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.socket_client is not None:
            if hasattr(self.socket_client, "disconnect"):
                await self.socket_client.disconnect()
            elif hasattr(self.socket_client, "close"):
                await self.socket_client.close()

    async def send_message(self, channel: str, text: str, thread_ts: str | None = None) -> None:
        self._ensure_bot_token()
        web_client = await self._get_web_client()
        kwargs: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        await web_client.chat_postMessage(**kwargs)

    async def poll(self) -> list[NormalizedMessage]:
        if not self.config.slack.enabled or not self._should_use_polling_mode():
            return []
        self._ensure_bot_token()
        await self._prepare_runtime_state()
        messages: list[NormalizedMessage] = []
        async with self._polling_http_client() as client:
            for channel in self.config.slack.channels:
                if not channel.channel_id:
                    continue
                messages.extend(await self._poll_channel(client, channel.channel_id, channel.include_threads))
        return messages

    async def handle_socket_request(self, request: Any) -> None:
        await self._ack_socket_request(request)
        task = asyncio.create_task(self._dispatch_socket_request(request))
        self._background_tasks.add(task)
        task.add_done_callback(lambda done: self._background_tasks.discard(done))

    def normalize_event(self, event: dict[str, Any], *, mode: str, event_id: str | None = None) -> NormalizedMessage | None:
        event_type = str(event.get("type", ""))
        if event_type not in {"message", "app_mention"}:
            return None
        if event.get("subtype"):
            return None
        user = event.get("user")
        if self.config.slack.ignore_bot_messages and (event.get("bot_id") or user is None):
            return None
        if self._bot_user_id and user == self._bot_user_id:
            return None
        channel_id = str(event.get("channel", ""))
        channel_type = str(event.get("channel_type") or "")
        is_dm = channel_type == "im"
        if is_dm:
            if not self.config.slack.behavior.allow_dms:
                return None
        elif not self._channel_allowed(channel_id):
            return None
        thread_ts = event.get("thread_ts")
        if thread_ts and thread_ts != event.get("ts") and not self._include_threads_for_channel(channel_id):
            return None
        explicit_address = self._extract_explicit_address(event, is_dm=is_dm)
        if explicit_address is None:
            return None
        return NormalizedMessage(
            id=new_id("msg"),
            source=MessageSource.SLACK,
            type=MessageType.MESSAGE_CREATED,
            payload={
                "channel": channel_id,
                "user": user,
                "raw_text": event.get("text", ""),
                "text": explicit_address["text"],
                "ts": event.get("ts"),
                "thread_ts": thread_ts,
                "event_ts": event.get("event_ts") or event.get("ts"),
                "is_dm": is_dm,
                "mentioned_bot": explicit_address["mentioned_bot"],
                "matched_prefix": explicit_address["matched_prefix"],
            },
            correlation_id=new_id("corr"),
            parent_message_id=None,
            metadata={
                "connector": "slack",
                "mode": mode,
                **({"event_id": event_id} if event_id else {}),
            },
            created_at=utc_now(),
        )

    async def _dispatch_socket_request(self, request: Any) -> None:
        if getattr(request, "type", None) != "events_api":
            return
        payload = getattr(request, "payload", {}) or {}
        event = payload.get("event") or {}
        dedupe_key = self._dedupe_key(payload.get("event_id"), event)
        if self._is_duplicate(dedupe_key):
            return
        normalized = self.normalize_event(event, mode="socket", event_id=payload.get("event_id"))
        if normalized is None:
            return
        self._mark_processed(dedupe_key)
        try:
            await self.dispatch_message(normalized)
        except Exception:
            logger.exception("Slack Socket Mode dispatch failed for event %s", payload.get("event_id") or dedupe_key)

    async def _ack_socket_request(self, request: Any) -> None:
        if self.socket_client is None or not hasattr(self.socket_client, "send_socket_mode_response"):
            return
        response = self._socket_mode_response(getattr(request, "envelope_id", ""))
        await self.socket_client.send_socket_mode_response(response)

    def _register_socket_listener(self) -> None:
        listeners = getattr(self.socket_client, "socket_mode_request_listeners", None)
        if listeners is None:
            return
        if self.handle_socket_request not in listeners:
            listeners.append(self.handle_socket_request)

    async def _polling_loop(self) -> None:
        while True:
            for message in await self.poll():
                await self.dispatch_message(message)
            await asyncio.sleep(self.config.slack.polling.poll_interval_seconds)

    async def _poll_channel(self, client: httpx.AsyncClient, channel_id: str, include_threads: bool) -> list[NormalizedMessage]:
        scope = f"channel:{channel_id}"
        cursor = self.cursors.get("slack", scope)
        params: dict[str, Any] = {"channel": channel_id, "oldest": cursor.cursor if cursor else "0"}
        response = await client.get("/conversations.history", params=params)
        response.raise_for_status()
        items = response.json().get("messages", [])
        normalized = self._normalize_polled_messages(channel_id, items, None)
        if include_threads:
            for item in items:
                thread_ts = item.get("thread_ts")
                if thread_ts and thread_ts == item.get("ts"):
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
        return self._normalize_polled_messages(channel_id, items, thread_ts)

    def _normalize_polled_messages(
        self,
        channel_id: str,
        items: list[dict[str, Any]],
        thread_ts: str | None,
    ) -> list[NormalizedMessage]:
        normalized: list[NormalizedMessage] = []
        for item in items:
            event = {
                "type": "message",
                "channel": channel_id,
                "user": item.get("user"),
                "text": item.get("text", ""),
                "ts": item.get("ts"),
                "thread_ts": thread_ts or item.get("thread_ts"),
                "event_ts": item.get("ts"),
                "bot_id": item.get("bot_id"),
                "subtype": item.get("subtype"),
                "channel_type": item.get("channel_type", "channel"),
            }
            message = self.normalize_event(event, mode="polling")
            if message is not None:
                normalized.append(message)
        return normalized

    def _channel_allowed(self, channel_id: str) -> bool:
        if not self.config.slack.channels:
            return True
        return channel_id in self._allowed_channel_ids

    def _include_threads_for_channel(self, channel_id: str) -> bool:
        if not channel_id:
            return True
        return self._channel_thread_policy.get(channel_id, True)

    def _dedupe_key(self, event_id: str | None, event: dict[str, Any]) -> str:
        if event_id:
            return f"slack:{event_id}"
        return f"slack:{event.get('channel', '')}:{event.get('ts', '')}"

    def _is_duplicate(self, dedupe_key: str) -> bool:
        return self.cursors.get("slack_event", dedupe_key) is not None

    def _mark_processed(self, dedupe_key: str) -> None:
        self.cursors.upsert(
            ConnectorCursorRecord(
                connector="slack_event",
                scope=dedupe_key,
                cursor=utc_now().isoformat(),
                updated_at=utc_now(),
            )
        )

    def _should_use_socket_mode(self) -> bool:
        return self.config.slack.mode == SlackMode.SOCKET

    def _should_use_polling_mode(self) -> bool:
        return self.config.slack.mode == SlackMode.POLLING or self.config.slack.polling.enabled

    def _ensure_socket_tokens(self) -> None:
        self._ensure_bot_token()
        if not self.config.settings.slack_app_token:
            raise RuntimeError("Slack Socket Mode requires SLACK_APP_TOKEN to be set.")

    def _ensure_bot_token(self) -> None:
        if not self.config.settings.slack_bot_token:
            raise RuntimeError("Slack integration requires SLACK_BOT_TOKEN to be set.")

    async def _get_web_client(self) -> Any:
        if self.web_client is not None:
            return self.web_client
        if AsyncWebClient is None:
            raise RuntimeError("slack_sdk is required for Slack Web API access.")
        self.web_client = AsyncWebClient(token=self.config.settings.slack_bot_token)
        return self.web_client

    async def _prepare_runtime_state(self) -> None:
        web_client = await self._get_web_client()
        if not self._bot_user_id:
            auth = await web_client.auth_test()
            self._bot_user_id = str(auth["user_id"])
        await self._resolve_allowed_channels(web_client)

    async def _resolve_allowed_channels(self, web_client: Any) -> None:
        cached_names = self._load_channel_cache()
        self._channel_name_cache = dict(cached_names)
        unresolved = [channel for channel in self.config.slack.channels if not channel.channel_id and channel.channel_name]
        if unresolved:
            self._channel_name_cache.update(await self._fetch_channel_name_map(web_client))
            self._save_channel_cache(self._channel_name_cache)
        allowed_channel_ids: set[str] = set()
        channel_thread_policy: dict[str, bool] = {}
        for channel in self.config.slack.channels:
            resolved_id = channel.channel_id
            if not resolved_id and channel.channel_name:
                resolved_id = self._channel_name_cache.get(channel.channel_name)
                if not resolved_id:
                    raise RuntimeError(f"Slack channel_name '{channel.channel_name}' could not be resolved to a channel ID.")
                channel.channel_id = resolved_id
            if resolved_id:
                allowed_channel_ids.add(resolved_id)
                channel_thread_policy[resolved_id] = channel.include_threads
        self._allowed_channel_ids = allowed_channel_ids
        self._channel_thread_policy = channel_thread_policy

    async def _fetch_channel_name_map(self, web_client: Any) -> dict[str, str]:
        mapping: dict[str, str] = {}
        cursor: str | None = None
        while True:
            response = await web_client.conversations_list(types="public_channel,private_channel", limit=1000, cursor=cursor)
            for channel in response.get("channels", []) or []:
                name = channel.get("name")
                channel_id = channel.get("id")
                if name and channel_id:
                    mapping[str(name)] = str(channel_id)
            cursor = response.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                break
        return mapping

    def _extract_explicit_address(self, event: dict[str, Any], *, is_dm: bool) -> dict[str, Any] | None:
        raw_text = str(event.get("text") or "")
        if not raw_text.strip():
            return None
        mention_token = f"<@{self._bot_user_id}>" if self._bot_user_id else ""
        mentioned_bot = bool(mention_token and mention_token in raw_text)
        matched_prefix = self._match_prefix(raw_text)
        requires_mention = self.config.slack.behavior.requires_mention
        if is_dm:
            if not self.config.slack.behavior.allow_dms:
                return None
            if requires_mention and not (mentioned_bot or matched_prefix):
                return None
        elif event.get("type") == "app_mention":
            mentioned_bot = True
        elif requires_mention and not (mentioned_bot or matched_prefix):
            return None
        cleaned_text = raw_text
        if mention_token:
            cleaned_text = cleaned_text.replace(mention_token, " ")
        if matched_prefix:
            cleaned_text = cleaned_text[len(matched_prefix) :]
        cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
        if not cleaned_text:
            return None
        return {"text": cleaned_text, "mentioned_bot": mentioned_bot, "matched_prefix": matched_prefix}

    def _match_prefix(self, raw_text: str) -> str | None:
        for prefix in self.config.slack.behavior.prefixes:
            if raw_text.startswith(prefix):
                return prefix
        return None

    def _channel_cache_path(self) -> Path:
        return Path(self.config.settings.sqlite_path).resolve().parent / "slack_channels.json"

    def _load_channel_cache(self) -> dict[str, str]:
        path = self._channel_cache_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items()}

    def _save_channel_cache(self, mapping: dict[str, str]) -> None:
        path = self._channel_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")

    def _polling_http_client(self) -> httpx.AsyncClient | _SlackHttpClientContext:
        if self.polling_client:
            return _SlackHttpClientContext(self.polling_client)
        return httpx.AsyncClient(
            base_url="https://slack.com/api",
            headers={"Authorization": f"Bearer {self.config.settings.slack_bot_token}"},
            timeout=30.0,
        )

    def _socket_mode_response(self, envelope_id: str) -> Any:
        if SocketModeResponse is not None:
            return SocketModeResponse(envelope_id=envelope_id)
        return {"envelope_id": envelope_id}


class _SlackHttpClientContext:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self.client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None
