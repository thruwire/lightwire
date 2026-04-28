from __future__ import annotations

from app.models import AgentOutputRecord, MessageSource, MessageType, NormalizedMessage
from app.utils.ids import new_id
from app.utils.time import utc_now


class OutputHandler:
    def to_message(self, output: AgentOutputRecord, parent_message_id: str) -> NormalizedMessage:
        # Agent output is normalized back into the same event shape so downstream routing stays connector-agnostic.
        return NormalizedMessage(
            id=new_id("msg"),
            source=MessageSource.AGENT_OUTPUT,
            type=MessageType.AGENT_COMPLETED,
            payload={"content": output.content},
            correlation_id=output.correlation_id,
            parent_message_id=parent_message_id,
            metadata={"agent_id": output.agent_id, **output.metadata},
            created_at=utc_now(),
        )
