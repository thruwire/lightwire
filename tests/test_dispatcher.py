import asyncio

from app.config import Settings
from app.main import build_state
from app.models import MessageSource, MessageType, NormalizedMessage
from app.utils.ids import new_id
from app.utils.time import utc_now


def test_dispatcher_runs_demo_chain(tmp_path) -> None:
    state = build_state(Settings(sqlite_path=str(tmp_path / "demo.db"), workspace_path="workspace"))
    message = NormalizedMessage(
        id=new_id("msg"),
        source=MessageSource.API,
        type=MessageType.MESSAGE_CREATED,
        payload={"text": "memory handoffs"},
        correlation_id=new_id("corr"),
        parent_message_id=None,
        metadata={},
        created_at=utc_now(),
    )
    session_ids = asyncio.run(state.dispatcher.dispatch(message))
    assert len(session_ids) == 1
    rows = state.db.fetchall("SELECT agent_id FROM sessions ORDER BY created_at")
    assert [row["agent_id"] for row in rows] == ["researcher", "analyst", "brief_writer"]
