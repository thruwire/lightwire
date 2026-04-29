from __future__ import annotations

from app.memory.path_extractor import classify_memory_paths, extract_memory_paths
from app.models import AgentOutputRecord, ExtractedMemoryPaths, MessageSource, MessageType, NormalizedMessage
from app.utils.ids import new_id
from app.utils.time import utc_now


class OutputHandler:
    def build_summary(self, content: str, max_length: int = 280) -> str:
        compact = " ".join(content.split())
        if len(compact) <= max_length:
            return compact
        return compact[: max_length - 3].rstrip() + "..."

    def extract_paths(self, content: str) -> ExtractedMemoryPaths:
        return classify_memory_paths(extract_memory_paths(content))

    def extract_written_paths(self, raw: dict[str, object]) -> ExtractedMemoryPaths:
        events = raw.get("events")
        if not isinstance(events, list):
            return ExtractedMemoryPaths()
        paths: list[str] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") == "agent.tool_use" and event.get("name") == "write":
                tool_input = event.get("input")
                if isinstance(tool_input, dict):
                    file_path = tool_input.get("file_path")
                    if isinstance(file_path, str):
                        paths.append(file_path)
            for block in event.get("content", []) or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if block.get("name") != "write":
                    continue
                tool_input = block.get("input")
                if not isinstance(tool_input, dict):
                    continue
                file_path = tool_input.get("file_path")
                if isinstance(file_path, str):
                    paths.append(file_path)
        return classify_memory_paths(paths)

    def to_message(self, output: AgentOutputRecord, parent_message_id: str) -> NormalizedMessage:
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
                "artifacts": output.artifacts,
                "handoffs": output.handoffs,
                "memory_paths": output.memory_paths,
            },
            correlation_id=output.correlation_id,
            parent_message_id=parent_message_id,
            metadata={"agent_id": output.agent_id, **output.metadata},
            created_at=utc_now(),
        )
