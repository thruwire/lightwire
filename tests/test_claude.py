import asyncio
import json

from app.config import Settings
from app.main import build_state
from app.models import ProviderStateRecord
from app.utils.time import utc_now


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


def test_ant_json_parser_accepts_extra_stdout_and_returns_last_object(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=True,
        )
    )
    output = "\n".join(
        [
            "INFO: reusing existing resource",
            json.dumps({"items": [{"id": "old"}]}),
            json.dumps({"id": "final", "name": "Responder"}),
        ]
    )
    parsed = state.claude._parse_ant_json_output(output, ["beta:agents", "update"])
    assert parsed["id"] == "final"


def test_live_agent_deploy_updates_existing_slug_and_archives_duplicates(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=False,
        )
    )
    calls: list[tuple[list[str], dict | None]] = []

    async def fake_require_ant() -> None:
        return None

    async def fake_run_ant_json(args: list[str], payload: dict | None = None) -> dict | list[dict]:
        calls.append((args, payload))
        if args[:2] == ["beta:agents", "list"]:
            return [
                {
                    "id": "agent_old",
                    "name": "Researcher",
                    "version": 1,
                    "updated_at": "2026-04-28T12:00:00Z",
                    "metadata": {"managed_agents_repo": "thruflow", "managed_agents_slug": "researcher"},
                },
                {
                    "id": "agent_new",
                    "name": "Researcher",
                    "version": 2,
                    "updated_at": "2026-04-29T12:00:00Z",
                    "metadata": {"managed_agents_repo": "thruflow", "managed_agents_slug": "researcher"},
                },
            ]
        if args[:2] == ["beta:agents", "archive"]:
            return {"id": args[3]}
        if args[:2] == ["beta:agents", "update"]:
            return {"id": "agent_new", "version": 3, "name": "Researcher"}
        raise AssertionError(f"unexpected command: {args}")

    state.claude._require_ant = fake_require_ant  # type: ignore[method-assign]
    state.claude._run_ant_json = fake_run_ant_json  # type: ignore[method-assign]

    result = asyncio.run(state.claude.create_or_update_agent("researcher"))

    assert result["id"] == "agent_new"
    assert any(args[:2] == ["beta:agents", "archive"] and args[3] == "agent_old" for args, _ in calls)
    assert any(args[:2] == ["beta:agents", "update"] and args[3] == "agent_new" for args, _ in calls)
    assert not any(args[:2] == ["beta:agents", "create"] for args, _ in calls)


def test_live_resource_ensure_reuses_existing_cached_resources(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=False,
        )
    )
    state.provider_state.upsert(
        ProviderStateRecord(
            provider="claude_managed_agents",
            resource_type="environment",
            logical_key="default",
            external_id="env_cached",
            metadata={"workspace": "workspace"},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    state.provider_state.upsert(
        ProviderStateRecord(
            provider="claude_managed_agents",
            resource_type="memory_store",
            logical_key="shared",
            external_id="mem_cached",
            metadata={"workspace": "workspace"},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )

    create_environment_calls: list[str] = []
    create_memory_store_calls: list[str] = []

    async def fake_create_environment(name: str) -> str:
        create_environment_calls.append(name)
        return "env_live"

    async def fake_create_memory_store(name: str) -> str:
        create_memory_store_calls.append(name)
        return "mem_live"

    state.claude.create_environment = fake_create_environment  # type: ignore[method-assign]
    state.claude.create_memory_store = fake_create_memory_store  # type: ignore[method-assign]

    environment_id, memory_store_id = asyncio.run(state.resources.ensure())

    assert environment_id == "env_cached"
    assert memory_store_id == "mem_cached"
    assert create_environment_calls == []
    assert create_memory_store_calls == []
    assert state.provider_state.get("claude_managed_agents", "environment", "default").external_id == "env_cached"
    assert state.provider_state.get("claude_managed_agents", "memory_store", "shared").external_id == "mem_cached"


def test_deploy_verify_remote_recreates_missing_cached_core_resources(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=False,
        )
    )
    state.provider_state.upsert(
        ProviderStateRecord(
            provider="claude_managed_agents",
            resource_type="environment",
            logical_key="default",
            external_id="env_stale",
            metadata={"workspace": "workspace"},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    state.provider_state.upsert(
        ProviderStateRecord(
            provider="claude_managed_agents",
            resource_type="memory_store",
            logical_key="shared",
            external_id="mem_stale",
            metadata={"workspace": "workspace"},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )

    async def fake_environment_exists(_: str) -> bool:
        return False

    async def fake_memory_store_exists(_: str) -> bool:
        return False

    async def fake_create_environment(_: str) -> str:
        return "env_new"

    async def fake_create_memory_store(_: str) -> str:
        return "mem_new"

    state.claude.environment_exists = fake_environment_exists  # type: ignore[method-assign]
    state.claude.memory_store_exists = fake_memory_store_exists  # type: ignore[method-assign]
    state.claude.create_environment = fake_create_environment  # type: ignore[method-assign]
    state.claude.create_memory_store = fake_create_memory_store  # type: ignore[method-assign]

    environment_id, memory_store_id = asyncio.run(state.resources.ensure(verify_remote=True))

    assert environment_id == "env_new"
    assert memory_store_id == "mem_new"
    assert state.provider_state.get("claude_managed_agents", "environment", "default").external_id == "env_new"
    assert state.provider_state.get("claude_managed_agents", "memory_store", "shared").external_id == "mem_new"


def test_deploy_verify_remote_recreates_missing_cached_vault_and_credential(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=False,
        )
    )
    state.provider_state.upsert(
        ProviderStateRecord(
            provider="claude_managed_agents",
            resource_type="vault",
            logical_key="vault:researcher",
            external_id="vault_stale",
            metadata={"workspace": "workspace", "agent_id": "researcher"},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    state.provider_state.upsert(
        ProviderStateRecord(
            provider="claude_managed_agents",
            resource_type="vault_credential",
            logical_key="vault_credential:researcher:external_research",
            external_id="cred_stale",
            metadata={"vault_id": "vault_stale", "server_name": "external_research"},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )

    async def fake_vault_exists(_: str) -> bool:
        return False

    async def fake_vault_credential_exists(_: str, __: str) -> bool:
        return False

    async def fake_create_vault(*, display_name: str, metadata: dict[str, str]) -> str:
        assert metadata["agent_id"] == "researcher"
        return "vault_new"

    async def fake_create_or_update_vault_credential(
        *,
        vault_id: str,
        display_name: str,
        metadata: dict[str, str],
        auth: dict[str, object],
    ) -> str:
        assert vault_id == "vault_new"
        assert metadata["server_name"] == "external_research"
        assert auth["type"] == "static_bearer"
        return "cred_new"

    state.claude.vault_exists = fake_vault_exists  # type: ignore[method-assign]
    state.claude.vault_credential_exists = fake_vault_credential_exists  # type: ignore[method-assign]
    state.claude.create_vault = fake_create_vault  # type: ignore[method-assign]
    state.claude.create_or_update_vault_credential = fake_create_or_update_vault_credential  # type: ignore[method-assign]

    import os
    previous = os.environ.get("EXTERNAL_RESEARCH_MCP_TOKEN")
    os.environ["EXTERNAL_RESEARCH_MCP_TOKEN"] = "token"
    try:
        vault_ids = asyncio.run(state.resources.ensure_agent_vaults("researcher", verify_remote=True))
    finally:
        if previous is None:
            os.environ.pop("EXTERNAL_RESEARCH_MCP_TOKEN", None)
        else:
            os.environ["EXTERNAL_RESEARCH_MCP_TOKEN"] = previous

    assert vault_ids == ["vault_new"]
    assert state.provider_state.get("claude_managed_agents", "vault", "vault:researcher").external_id == "vault_new"
    assert (
        state.provider_state.get(
            "claude_managed_agents",
            "vault_credential",
            "vault_credential:researcher:external_research",
        ).external_id
        == "cred_new"
    )
