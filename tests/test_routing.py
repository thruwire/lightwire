from pathlib import Path

import pytest

from app.config import Settings, load_runtime_config
from app.models import MessageSource, MessageType, NormalizedMessage
from app.routing.rules import route_matches
from app.routing.router import Router
from app.routing.templates import render_template
from app.utils.ids import new_id
from app.utils.time import utc_now


def build_message(**overrides):
    data = {
        "id": new_id("msg"),
        "source": MessageSource.API,
        "type": MessageType.MESSAGE_CREATED,
        "payload": {"text": "hello"},
        "correlation_id": new_id("corr"),
        "parent_message_id": None,
        "metadata": {},
        "created_at": utc_now(),
    }
    data.update(overrides)
    return NormalizedMessage(**data)


def test_route_matching() -> None:
    config = load_runtime_config(Settings(sqlite_path=":memory:", workspace_path="workspace"))
    route = next(route for route in config.routes.routes if route.id == "api_to_research")
    assert route_matches(build_message(), route)


def test_telegram_route_matching() -> None:
    config = load_runtime_config(Settings(sqlite_path=":memory:", workspace_path="workspace"))
    route = next(route for route in config.routes.routes if route.id == "telegram_to_research")
    assert route_matches(build_message(source=MessageSource.TELEGRAM), route)


def test_prompt_rendering() -> None:
    rendered = render_template(
        "Hello {{ payload.text }} {{ correlation_id }} {{ parent_message_id }}",
        {"payload": {"text": "world"}, "correlation_id": "corr_1", "parent_message_id": "msg_0"},
    )
    assert rendered == "Hello world corr_1 msg_0"


def test_agent_output_triggers_downstream_route() -> None:
    config = load_runtime_config(Settings(sqlite_path=":memory:", workspace_path="workspace"))
    router = Router(config)
    message = build_message(
        source=MessageSource.AGENT_OUTPUT,
        type=MessageType.AGENT_COMPLETED,
        payload={
            "content": "research",
            "upstream_outputs": [
                {
                    "source_agent_id": "researcher",
                    "source_step_id": "sess_1",
                    "content": "research",
                    "metadata": {"route_id": "api_to_research"},
                }
            ],
        },
        metadata={"agent_id": "researcher"},
    )
    dispatches = router.resolve(message)
    assert len(dispatches) == 1
    assert dispatches[0].agent_id == "analyst"


def test_downstream_template_receives_upstream_outputs_text() -> None:
    config = load_runtime_config(Settings(sqlite_path=":memory:", workspace_path="workspace"))
    router = Router(config)
    message = build_message(
        source=MessageSource.AGENT_OUTPUT,
        type=MessageType.AGENT_COMPLETED,
        payload={
            "upstream_outputs": [
                {
                    "source_agent_id": "researcher",
                    "source_step_id": "sess_1",
                    "content": "Research complete.",
                    "metadata": {"route_id": "api_to_research"},
                }
            ],
            "content": "Research complete.",
            "summary": "Research complete.",
        },
        metadata={"agent_id": "researcher"},
    )
    dispatch = router.resolve(message)[0]
    assert '<upstream_output source_agent="researcher" source_step="sess_1">' in dispatch.prompt
    assert "Research complete." in dispatch.prompt


def test_missing_template_produces_clear_error(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "agents" / "researcher").mkdir(parents=True)
    (workspace / "skills").mkdir()
    (workspace / "prompt_templates").mkdir()
    (workspace / "tools.yaml").write_text("built_in: {}\nmcp_servers: {}", encoding="utf-8")
    (workspace / "agents" / "researcher" / "config.yaml").write_text(
        """
agent_id: researcher
enabled: true
provider: claude_managed_agents
memory:
  access: read_write
""".strip(),
        encoding="utf-8",
    )
    (workspace / "agents" / "researcher" / "AGENT.md").write_text("You are the Researcher agent.", encoding="utf-8")
    (workspace / "routes.yaml").write_text(
        """
routes:
  - id: broken
    enabled: true
    match:
      source: api
      type: message.created
    target:
      agent_id: researcher
      prompt_template: prompt_templates/missing.md
""".strip(),
        encoding="utf-8",
    )
    (workspace / "heartbeats.yaml").write_text("heartbeats: []", encoding="utf-8")
    (workspace / "slack.yaml").write_text("enabled: false", encoding="utf-8")
    config = load_runtime_config(Settings(sqlite_path=":memory:", workspace_path=str(workspace)))
    router = Router(config)

    with pytest.raises(FileNotFoundError, match="Prompt template 'prompt_templates/missing.md' was not found"):
        router.resolve(build_message())


def test_demo_templates_use_upstream_outputs_text() -> None:
    research_to_analysis = Path("workspace/prompt_templates/research_to_analysis.md").read_text(encoding="utf-8")
    analysis_to_brief = Path("workspace/prompt_templates/analysis_to_brief.md").read_text(encoding="utf-8")
    assert "{{ upstream_outputs_text }}" in research_to_analysis
    assert "{{ upstream_outputs_text }}" in analysis_to_brief
