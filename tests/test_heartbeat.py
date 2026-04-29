import asyncio

from app.config import Settings
from app.main import build_state


def test_heartbeat_scheduling(tmp_path) -> None:
    state = build_state(Settings(sqlite_path=str(tmp_path / "hb.db"), workspace_path="workspace", thruflow_fake_claude=True))
    state.heartbeat_scheduler.initialize()
    session_ids = asyncio.run(state.heartbeat_scheduler.trigger("research_scout"))
    assert len(session_ids) == 1
