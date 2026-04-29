from app.models import AgentOutputRecord, RoutedOutput
from app.utils.time import utc_now
from app.workers.output_handler import OutputHandler


def test_output_handler_builds_routed_output_from_record() -> None:
    output = AgentOutputRecord(
        id="out_1",
        session_id="sess_1",
        agent_id="researcher",
        correlation_id="corr_1",
        content="Research complete.",
        summary="Research complete.",
        metadata={"route_id": "api_to_research"},
        created_at=utc_now(),
    )
    routed = OutputHandler().routed_output_from_record(output)
    assert routed == RoutedOutput(
        source_agent_id="researcher",
        source_step_id="sess_1",
        content="Research complete.",
        metadata={"route_id": "api_to_research"},
    )


def test_output_handler_emits_upstream_outputs_in_agent_output_message() -> None:
    output = AgentOutputRecord(
        id="out_1",
        session_id="sess_1",
        agent_id="researcher",
        correlation_id="corr_1",
        content="Research complete.",
        summary="Research complete.",
        metadata={"route_id": "api_to_research"},
        created_at=utc_now(),
    )
    upstream_outputs = [
        RoutedOutput(
            source_agent_id="researcher",
            source_step_id="sess_1",
            content="Research complete.",
            metadata={"route_id": "api_to_research"},
        )
    ]
    message = OutputHandler().to_message(output, parent_message_id="msg_1", upstream_outputs=upstream_outputs)
    assert message.payload["upstream_outputs"] == [
        {
            "source_agent_id": "researcher",
            "source_step_id": "sess_1",
            "content": "Research complete.",
            "metadata": {"route_id": "api_to_research"},
        }
    ]
