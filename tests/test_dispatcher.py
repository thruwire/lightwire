import asyncio
from types import SimpleNamespace

from app.config import Settings
from app.main import build_state
from app.models import MessageSource, MessageType, NormalizedMessage
from app.utils.ids import new_id
from app.utils.time import utc_now


def test_dispatcher_runs_demo_chain(tmp_path) -> None:
    state = build_state(Settings(sqlite_path=str(tmp_path / "demo.db"), workspace_path="workspace", lightwire_fake_claude=True))
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
    outputs = state.db.fetchall("SELECT content FROM agent_outputs ORDER BY created_at")
    assert "Research complete" in outputs[0]["content"]
    assert "Analysis complete" in outputs[1]["content"]
    assert "Executive brief complete" in outputs[2]["content"]

def test_dispatcher_accumulates_upstream_outputs_for_chained_agents(tmp_path) -> None:
    state = build_state(
        Settings(sqlite_path=str(tmp_path / "upstream.db"), workspace_path="workspace", lightwire_fake_claude=True)
    )
    message = NormalizedMessage(
        id=new_id("msg"),
        source=MessageSource.API,
        type=MessageType.MESSAGE_CREATED,
        payload={"text": "direct handoffs"},
        correlation_id=new_id("corr"),
        parent_message_id=None,
        metadata={},
        created_at=utc_now(),
    )
    session_ids = asyncio.run(state.dispatcher.dispatch(message))
    assert len(session_ids) == 1
    messages = state.db.fetchall("SELECT payload FROM messages WHERE source = 'agent_output' ORDER BY created_at")
    assert len(messages) == 3
    analyst_payload = messages[0]["payload"]
    brief_payload = messages[1]["payload"]
    assert "upstream_outputs" in analyst_payload
    assert "upstream_outputs" in brief_payload


def test_non_slack_root_can_reply_to_explicit_slack_channel(tmp_path) -> None:
    state = build_state(
        Settings(sqlite_path=str(tmp_path / "reply.db"), workspace_path="workspace", lightwire_fake_claude=True)
    )
    sent_messages: list[dict[str, object]] = []

    class FakeSlackConnector:
        def __init__(self) -> None:
            self.config = SimpleNamespace(slack=SimpleNamespace(send_replies=True))

        async def resolve_channel(self, *, channel_id: str | None = None, channel_name: str | None = None) -> str | None:
            if channel_id:
                return channel_id
            if channel_name == "lightwire":
                return "C123456"
            return None

        async def send_message(self, channel: str, text: str, thread_ts: str | None = None) -> None:
            sent_messages.append({"channel": channel, "text": text, "thread_ts": thread_ts})

    state.dispatcher.slack_connector = FakeSlackConnector()
    heartbeat = NormalizedMessage(
        id=new_id("msg"),
        source=MessageSource.HEARTBEAT,
        type=MessageType.HEARTBEAT_TICK,
        payload={},
        correlation_id=new_id("corr"),
        parent_message_id=None,
        metadata={},
        created_at=utc_now(),
    )
    output_message = NormalizedMessage(
        id=new_id("msg"),
        source=MessageSource.AGENT_OUTPUT,
        type=MessageType.AGENT_COMPLETED,
        payload={"content": "Heartbeat result"},
        correlation_id=heartbeat.correlation_id,
        parent_message_id=heartbeat.id,
        metadata={"reply": {"connector": "slack", "mode": "final_output", "channel_name": "lightwire"}},
        created_at=utc_now(),
    )
    state.messages.create(heartbeat)
    asyncio.run(state.dispatcher._handle_reply(output_message, heartbeat))
    assert sent_messages == [{"channel": "C123456", "text": "Heartbeat result", "thread_ts": None}]
