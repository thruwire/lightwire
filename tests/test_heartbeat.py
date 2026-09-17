import asyncio
from contextlib import suppress

import pytest

from app.config import Settings
from app.main import build_state


def test_heartbeat_scheduling(tmp_path) -> None:
    state = build_state(Settings(sqlite_path=str(tmp_path / "hb.db"), workspace_path="workspace", lightwire_fake_claude=True))
    state.heartbeat_scheduler.initialize()
    session_ids = asyncio.run(state.heartbeat_scheduler.trigger("research_scout"))
    assert len(session_ids) == 1


@pytest.mark.asyncio
async def test_heartbeat_loop_continues_after_tick_failure(tmp_path) -> None:
    state = build_state(
        Settings(sqlite_path=str(tmp_path / "hb.db"), workspace_path="workspace", lightwire_fake_claude=True)
    )
    scheduler = state.heartbeat_scheduler
    scheduler.sleep_seconds = 0
    attempts = 0
    recovered = asyncio.Event()

    async def flaky_tick() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        recovered.set()

    scheduler.tick = flaky_tick  # type: ignore[method-assign]
    task = asyncio.create_task(scheduler._run_loop())
    await asyncio.wait_for(recovered.wait(), timeout=1)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    assert attempts >= 2
