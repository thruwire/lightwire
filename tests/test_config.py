from app.config import Settings, load_runtime_config


def test_load_runtime_config() -> None:
    settings = Settings(
        sqlite_path=":memory:",
        workspace_path="workspace",
    )
    config = load_runtime_config(settings)
    assert "researcher" in config.agents
    assert "structured_notes" in config.skills
    assert "external_research" in config.tools.mcp_servers
    assert "web_search" in config.tools.built_in
    assert config.agents["researcher"].memory.access == "read_write"
    assert "external_research" in config.agents["researcher"].tools.mcp
    assert "prompt_templates/api_to_research.md" in config.prompt_templates
    assert config.telegram.enabled is True
    assert config.telegram.allowed_chats[0].chat_id == "123456789"
