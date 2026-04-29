from app.config import Settings, load_runtime_config
from pathlib import Path


def test_load_runtime_config() -> None:
    settings = Settings(
        sqlite_path=":memory:",
        workspace_path="workspace",
    )
    config = load_runtime_config(settings)
    assert "researcher" in config.agents
    assert "structured_notes" in config.skills
    assert config.skills["structured_notes"].description
    assert "external_research" in config.tools.mcp_servers
    assert "web_search" in config.tools.built_in
    assert config.agents["researcher"].memory.access == "read_write"
    assert "external_research" in config.agents["researcher"].tools.mcp
    assert "prompt_templates/api_to_research.md" in config.prompt_templates
    assert config.telegram.enabled is True
    assert config.slack.mode.value == "socket"
    assert config.telegram.allowed_chats[0].chat_id == "123456789"


def test_readme_explains_control_plane_data_plane_and_no_json() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "control plane" in readme.lower()
    assert "data plane" in readme.lower()
    assert "Agents do not need to return JSON." in readme


def test_skills_load_from_skill_md_frontmatter() -> None:
    config = load_runtime_config(Settings(sqlite_path=":memory:", workspace_path="workspace"))
    structured_notes = config.skills["structured_notes"]
    assert structured_notes.skill_id == "structured_notes"
    assert "Use this skill when the task requires structured synthesis" in structured_notes.instructions
    assert "concise handoffs" in (structured_notes.description or "")
