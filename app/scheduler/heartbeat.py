from __future__ import annotations

import asyncio
from contextlib import suppress

from app.config import RuntimeConfig
from app.models import HeartbeatState, MessageSource, MessageType, NormalizedMessage
from app.repositories.heartbeats import HeartbeatRepository
from app.utils.ids import new_id
from app.utils.time import utc_now
from app.workers.dispatcher import Dispatcher


class HeartbeatScheduler:
    def __init__(
        self,
        config: RuntimeConfig,
        repository: HeartbeatRepository,
        dispatcher: Dispatcher,
        sleep_seconds: int = 1,
    ) -> None:
        self.config = config
        self.repository = repository
        self.dispatcher = dispatcher
        self.sleep_seconds = sleep_seconds
        self._task: asyncio.Task[None] | None = None

    def initialize(self) -> None:
        now = utc_now()
        for heartbeat in self.config.heartbeats.heartbeats:
            if not heartbeat.enabled:
                continue
            if not self.repository.get(heartbeat.id):
                # New heartbeats start due immediately so a fresh deployment can begin work without waiting a full interval.
                self.repository.upsert(
                    HeartbeatState(
                        heartbeat_id=heartbeat.id,
                        next_run_at=now,
                        last_run_at=None,
                    )
                )

    async def start(self) -> None:
        self.initialize()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _run_loop(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(self.sleep_seconds)

    async def tick(self) -> None:
        now = utc_now()
        for heartbeat in self.config.heartbeats.heartbeats:
            if not heartbeat.enabled:
                continue
            state = self.repository.get(heartbeat.id)
            if not state or state.next_run_at > now:
                continue
            await self.trigger(heartbeat.id)

    async def trigger(self, heartbeat_id: str) -> list[str]:
        heartbeat = next(item for item in self.config.heartbeats.heartbeats if item.id == heartbeat_id and item.enabled)
        now = utc_now()
        self.repository.upsert(
            HeartbeatState(
                heartbeat_id=heartbeat.id,
                # next_run_at is advanced before dispatch so repeated manual triggers do not double-book the same interval.
                next_run_at=now.__class__.fromtimestamp(now.timestamp() + heartbeat.interval_seconds, tz=now.tzinfo),
                last_run_at=now,
            )
        )
        message = NormalizedMessage(
            id=new_id("msg"),
            source=MessageSource.HEARTBEAT,
            type=MessageType.HEARTBEAT_TICK,
            payload={},
            correlation_id=new_id("corr"),
            parent_message_id=None,
            metadata={"heartbeat_id": heartbeat.id, "target_agent_id": heartbeat.target.agent_id},
            created_at=now,
        )
        return await self.dispatcher.dispatch(message)
