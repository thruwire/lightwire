from pathlib import Path

from app.db.sqlite import (
    SqliteAgentOutputRepository,
    SqliteConnectorCursorRepository,
    SqliteDatabase,
    SqliteHeartbeatRepository,
    SqliteMessageRepository,
    SqliteProviderStateRepository,
    SqliteSessionRepository,
)
from app.models import AgentOutputRecord, ConnectorCursorRecord, HeartbeatState, MessageSource, MessageType, NormalizedMessage, ProviderStateRecord, SessionRecord, SessionStatus
from app.utils.ids import new_id
from app.utils.time import utc_now


def test_repository_crud(tmp_path: Path) -> None:
    db = SqliteDatabase(str(tmp_path / "test.db"))
    db.initialize()
    messages = SqliteMessageRepository(db)
    sessions = SqliteSessionRepository(db)
    heartbeats = SqliteHeartbeatRepository(db)
    outputs = SqliteAgentOutputRepository(db)
    cursors = SqliteConnectorCursorRepository(db)
    provider_state = SqliteProviderStateRepository(db)

    message = NormalizedMessage(
        id=new_id("msg"),
        source=MessageSource.API,
        type=MessageType.MESSAGE_CREATED,
        payload={"text": "x"},
        correlation_id=new_id("corr"),
        parent_message_id=None,
        metadata={},
        created_at=utc_now(),
    )
    messages.create(message)
    assert messages.get(message.id).payload["text"] == "x"

    session = SessionRecord(
        id=new_id("sess"),
        route_id="route_1",
        agent_id="researcher",
        message_id=message.id,
        correlation_id=message.correlation_id,
        prompt="prompt",
        status=SessionStatus.PENDING,
        external_session_id=None,
        memory_store_id="shared-memory",
        output_message_id=None,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    sessions.create(session)
    sessions.update_status(session.id, SessionStatus.COMPLETED, external_session_id="remote_1")
    assert sessions.get(session.id).external_session_id == "remote_1"

    heartbeat = HeartbeatState(heartbeat_id="hb1", next_run_at=utc_now(), last_run_at=None)
    heartbeats.upsert(heartbeat)
    assert heartbeats.get("hb1").heartbeat_id == "hb1"

    output = AgentOutputRecord(
        id=new_id("out"),
        session_id=session.id,
        agent_id="researcher",
        correlation_id=message.correlation_id,
        content="done",
        metadata={"k": "v"},
        created_at=utc_now(),
    )
    outputs.create(output)
    assert outputs.get_by_session(session.id).content == "done"

    cursor = ConnectorCursorRecord(connector="telegram", scope="global", cursor="123.4", updated_at=utc_now())
    cursors.upsert(cursor)
    assert cursors.get("telegram", "global").cursor == "123.4"

    provider = ProviderStateRecord(
        provider="claude_managed_agents",
        resource_type="memory_store",
        logical_key="shared",
        external_id="memory_store_123",
        metadata={"workspace": "workspace"},
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    provider_state.upsert(provider)
    assert provider_state.get("claude_managed_agents", "memory_store", "shared").external_id == "memory_store_123"
