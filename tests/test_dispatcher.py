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
    outputs = state.db.fetchall("SELECT content, artifacts_json, handoffs_json FROM agent_outputs ORDER BY created_at")
    assert "/mnt/memory/artifacts/research/" in outputs[0]["content"]
    assert "/mnt/memory/artifacts/analysis/" in outputs[1]["content"]
    assert "/mnt/memory/artifacts/briefs/" in outputs[2]["content"]


def test_require_artifacts_blocks_downstream_routing(tmp_path) -> None:
    state = build_state(Settings(sqlite_path=str(tmp_path / "require.db"), workspace_path="workspace"))

    async def fake_run(request):
        from app.models import ClaudeSessionResult, SessionStatus

        return ClaudeSessionResult(
            external_session_id="claude_session_test",
            status=SessionStatus.COMPLETED,
            content="Completed without mentioning durable paths.",
            attached_memory_store_id=request.memory_store_id,
            raw={},
        )

    state.dispatcher.runner.session_service.run = fake_run
    route = next(route for route in state.router.config.routes.routes if route.id == "api_to_research")
    route.require_artifacts = True
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
    assert [row["agent_id"] for row in rows] == ["researcher"]
    output_row = state.db.fetchone("SELECT metadata FROM agent_outputs LIMIT 1")
    assert "required artifact paths were not mentioned" in output_row["metadata"]
