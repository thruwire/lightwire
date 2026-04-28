import asyncio

from app.config import Settings
from app.main import build_state


def test_mocked_claude_session_attaches_service_managed_shared_memory(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=True,
        )
    )
    environment_id, memory_store_id = asyncio.run(state.resources.ensure())
    assert environment_id.startswith("claude_env_")
    assert memory_store_id.startswith("memory_store_")
    again_environment_id, again_memory_store_id = asyncio.run(state.resources.ensure())
    assert again_environment_id == environment_id
    assert again_memory_store_id == memory_store_id
    vault_ids = asyncio.run(state.resources.ensure_agent_vaults("researcher"))
    assert len(vault_ids) == 1
    again_vault_ids = asyncio.run(state.resources.ensure_agent_vaults("researcher"))
    assert again_vault_ids == vault_ids
