from __future__ import annotations

from app.models import AgentOutputRecord, MessageSource, MessageType, NormalizedMessage, RoutedOutput
from app.utils.ids import new_id
from app.utils.time import utc_now


class OutputHandler:
    def build_summary(self, content: str, max_length: int = 280) -> str:
        compact = " ".join(content.split())
        if len(compact) <= max_length:
            return compact
        return compact[: max_length - 3].rstrip() + "..."

    def routed_output_from_record(self, output: AgentOutputRecord) -> RoutedOutput:
        return RoutedOutput(
            source_agent_id=output.agent_id,
            source_step_id=output.session_id,
            content=output.content,
            metadata={"route_id": output.metadata.get("route_id")},
        )

    def upstream_outputs_from_message(self, message: NormalizedMessage) -> list[RoutedOutput]:
        values = message.payload.get("upstream_outputs", [])
        if not isinstance(values, list):
            return []
        outputs: list[RoutedOutput] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            outputs.append(RoutedOutput.model_validate(value))
        return outputs

    def to_message(
        self,
        output: AgentOutputRecord,
        parent_message_id: str,
        upstream_outputs: list[RoutedOutput],
    ) -> NormalizedMessage:
        # Agent output is normalized back into the same event shape so downstream routing stays connector-agnostic.
        return NormalizedMessage(
            id=new_id("msg"),
            source=MessageSource.AGENT_OUTPUT,
            type=MessageType.AGENT_COMPLETED,
            payload={
                "agent_id": output.agent_id,
                "session_id": output.session_id,
                "route_id": output.metadata.get("route_id"),
                "content": output.content,
                "summary": output.summary,
                "upstream_outputs": [item.model_dump(mode="json") for item in upstream_outputs],
            },
            correlation_id=output.correlation_id,
            parent_message_id=parent_message_id,
            metadata={"agent_id": output.agent_id, **output.metadata},
            created_at=utc_now(),
        )
