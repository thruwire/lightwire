import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.main import build_state
from app.models import MCPServerAuthType, ProviderStateRecord
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


def test_live_agent_deploy_prefers_cached_agent_id(tmp_path) -> None:
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
        if args[:2] == ["beta:agents", "retrieve"]:
            return {"id": "agent_cached", "version": 5, "name": "Researcher"}
        if args[:2] == ["beta:agents", "update"]:
            return {"id": "agent_cached", "version": 6, "name": "Researcher"}
        raise AssertionError(f"unexpected command: {args}")

    state.claude._require_ant = fake_require_ant  # type: ignore[method-assign]
    state.claude._run_ant_json = fake_run_ant_json  # type: ignore[method-assign]

    result = asyncio.run(
        state.claude.deploy_agent(
            state.config.get_agent("researcher"),
            state.config.get_agent_system_prompt("researcher"),
            existing_agent_id="agent_cached",
        )
    )

    assert result["id"] == "agent_cached"
    assert any(args[:2] == ["beta:agents", "retrieve"] and args[3] == "agent_cached" for args, _ in calls)
    assert any(args[:2] == ["beta:agents", "update"] and args[3] == "agent_cached" for args, _ in calls)
    assert not any(args[:2] == ["beta:agents", "list"] for args, _ in calls)


def test_sdk_session_deletes_completed_remote_session_by_default(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=False,
        )
    )
    state.config.attach_provider_state("env_live", "mem_live")

    delete_calls: list[str] = []

    class FakeStream:
        def __enter__(self):
            return iter(
                [
                    SimpleNamespace(
                        type="agent.message",
                        content=[SimpleNamespace(type="text", text="done")],
                    ),
                    SimpleNamespace(type="session.status_idle"),
                ]
            )

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    class FakeEvents:
        def stream(self, session_id: str) -> FakeStream:
            assert session_id == "sesn_live"
            return FakeStream()

        def send(self, session_id: str, events: list[dict[str, object]]) -> None:
            assert session_id == "sesn_live"
            assert events[0]["type"] == "user.message"

    class FakeSessions:
        def __init__(self) -> None:
            self.events = FakeEvents()

        def create(self, **kwargs):
            assert kwargs["environment_id"] == "env_live"
            return SimpleNamespace(id="sesn_live")

        def delete(self, session_id: str):
            delete_calls.append(session_id)
            return SimpleNamespace(id=session_id, type="session_deleted")

    fake_client = SimpleNamespace(beta=SimpleNamespace(sessions=FakeSessions()))
    state.claude._get_sdk_client = lambda: fake_client  # type: ignore[method-assign]

    session_id, content, status, raw = state.claude._run_sdk_session(
        SimpleNamespace(
            agent_id="agent_live",
            task_prompt="Write a note",
            memory_store_id="mem_live",
            memory_access="read_write",
            correlation_id="corr_live",
            vault_ids=[],
            metadata={},
        )
    )

    assert session_id == "sesn_live"
    assert content == "done"
    assert str(status) == "SessionStatus.COMPLETED"
    assert delete_calls == ["sesn_live"]
    assert raw["cleanup"]["attempted"] is True
    assert raw["cleanup"]["deleted"] is True


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


def test_runtime_ready_raises_when_provider_state_missing(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=False,
        )
    )

    with pytest.raises(RuntimeError, match="Provider resources have not been deployed yet"):
        state.resources.assert_runtime_ready()


def test_runtime_ready_uses_cached_provider_state_without_mutation(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=False,
        )
    )
    state.config.agents = {"researcher": state.config.agents["researcher"].model_copy(deep=True)}
    state.config.agents["researcher"].tools.mcp = {}

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
    state.provider_state.upsert(
        ProviderStateRecord(
            provider="claude_managed_agents",
            resource_type="agent",
            logical_key="researcher",
            external_id="agent_cached",
            metadata={"name": "Researcher"},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )

    state.resources.assert_runtime_ready()

    assert state.config.get_provider_environment_id() == "env_cached"
    assert state.config.get_provider_memory_store_id() == "mem_cached"


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
            logical_key="shared",
            external_id="vault_stale",
            metadata={"workspace": "workspace", "vault_scope": "shared_mcp"},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    state.provider_state.upsert(
        ProviderStateRecord(
            provider="claude_managed_agents",
            resource_type="vault_credential",
            logical_key="shared:external_research",
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
        assert metadata["vault_scope"] == "shared_mcp"
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
    assert state.provider_state.get("claude_managed_agents", "vault", "shared").external_id == "vault_new"
    assert (
        state.provider_state.get(
            "claude_managed_agents",
            "vault_credential",
            "shared:external_research",
        ).external_id
        == "cred_new"
    )


def test_oauth_client_credentials_bootstrap_builds_refreshable_shared_credential(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=False,
        )
    )

    auth = state.config.tools.mcp_servers["external_research"].auth
    auth.type = MCPServerAuthType.MCP_OAUTH_CLIENT_CREDENTIALS_ENV
    auth.token_endpoint = "https://auth.example.com/oauth/token"
    auth.client_id_env_var = "EXTERNAL_RESEARCH_OAUTH_CLIENT_ID"
    auth.client_secret_env_var = "EXTERNAL_RESEARCH_OAUTH_CLIENT_SECRET"
    auth.token_endpoint_auth_method = "client_secret_post"
    auth.scope = "search:read"

    previous_client_id = os.environ.get("EXTERNAL_RESEARCH_OAUTH_CLIENT_ID")
    previous_client_secret = os.environ.get("EXTERNAL_RESEARCH_OAUTH_CLIENT_SECRET")
    os.environ["EXTERNAL_RESEARCH_OAUTH_CLIENT_ID"] = "client-id"
    os.environ["EXTERNAL_RESEARCH_OAUTH_CLIENT_SECRET"] = "client-secret"

    async def fake_mint(_: str) -> dict[str, str]:
        return {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_at": "2026-05-01T00:00:00Z",
            "scope": "search:read",
        }

    async def fake_create_vault(*, display_name: str, metadata: dict[str, str]) -> str:
        assert metadata["managed_agents_slug"] == "shared-mcp"
        return "vault_shared"

    created_credentials: list[dict[str, object]] = []

    async def fake_create_or_update_vault_credential(
        *,
        vault_id: str,
        display_name: str,
        metadata: dict[str, str],
        auth: dict[str, object],
    ) -> str:
        created_credentials.append(
            {
                "vault_id": vault_id,
                "display_name": display_name,
                "metadata": metadata,
                "auth": auth,
            }
        )
        return "cred_shared"

    state.resources._mint_client_credentials_token_pair = fake_mint  # type: ignore[method-assign]
    state.claude.create_vault = fake_create_vault  # type: ignore[method-assign]
    state.claude.create_or_update_vault_credential = fake_create_or_update_vault_credential  # type: ignore[method-assign]

    try:
        vault_ids = asyncio.run(state.resources.ensure_agent_vaults("researcher", verify_remote=True))
    finally:
        if previous_client_id is None:
            os.environ.pop("EXTERNAL_RESEARCH_OAUTH_CLIENT_ID", None)
        else:
            os.environ["EXTERNAL_RESEARCH_OAUTH_CLIENT_ID"] = previous_client_id
        if previous_client_secret is None:
            os.environ.pop("EXTERNAL_RESEARCH_OAUTH_CLIENT_SECRET", None)
        else:
            os.environ["EXTERNAL_RESEARCH_OAUTH_CLIENT_SECRET"] = previous_client_secret

    assert vault_ids == ["vault_shared"]
    assert len(created_credentials) == 1
    credential = created_credentials[0]
    assert credential["vault_id"] == "vault_shared"
    assert credential["metadata"]["managed_agents_slug"] == "shared-mcp:external_research"
    assert credential["auth"]["type"] == "mcp_oauth"
    assert credential["auth"]["access_token"] == "access-1"
    assert credential["auth"]["expires_at"] == "2026-05-01T00:00:00Z"
    assert credential["auth"]["refresh"]["refresh_token"] == "refresh-1"
    assert credential["auth"]["refresh"]["client_id"] == "client-id"
    assert credential["auth"]["refresh"]["token_endpoint_auth"]["client_secret"] == "client-secret"


def test_shared_vault_is_reused_across_agents(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=False,
        )
    )

    state.config.agents["analyst"].tools.mcp = {"external_research": state.config.agents["researcher"].tools.mcp["external_research"].model_copy(deep=True)}

    vault_creates: list[dict[str, str]] = []

    async def fake_create_vault(*, display_name: str, metadata: dict[str, str]) -> str:
        vault_creates.append(metadata)
        return "vault_shared"

    async def fake_create_or_update_vault_credential(
        *,
        vault_id: str,
        display_name: str,
        metadata: dict[str, str],
        auth: dict[str, object],
    ) -> str:
        return "cred_shared"

    state.claude.create_vault = fake_create_vault  # type: ignore[method-assign]
    state.claude.create_or_update_vault_credential = fake_create_or_update_vault_credential  # type: ignore[method-assign]

    previous = os.environ.get("EXTERNAL_RESEARCH_MCP_TOKEN")
    os.environ["EXTERNAL_RESEARCH_MCP_TOKEN"] = "token"
    try:
        first = asyncio.run(state.resources.ensure_agent_vaults("researcher", verify_remote=False))
        second = asyncio.run(state.resources.ensure_agent_vaults("analyst", verify_remote=False))
    finally:
        if previous is None:
            os.environ.pop("EXTERNAL_RESEARCH_MCP_TOKEN", None)
        else:
            os.environ["EXTERNAL_RESEARCH_MCP_TOKEN"] = previous

    assert first == ["vault_shared"]
    assert second == ["vault_shared"]
    assert len(vault_creates) == 1


def test_deployment_service_applies_and_persists_agent_ids(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=False,
        )
    )

    ensure_calls: list[bool] = []
    vault_calls: list[bool] = []

    async def fake_ensure(*, verify_remote: bool = False) -> tuple[str, str]:
        ensure_calls.append(verify_remote)
        return "env_id", "mem_id"

    async def fake_ensure_all_agent_vaults(*, verify_remote: bool = False) -> None:
        vault_calls.append(verify_remote)

    deploy_calls: list[str | None] = []

    async def fake_deploy_agent(agent, system_prompt: str, *, existing_agent_id: str | None = None) -> dict[str, object]:
        deploy_calls.append(existing_agent_id)
        return {"id": f"agent_remote_{agent.agent_id}", "name": agent.agent_id.title(), "version": 7}

    async def fake_list_managed_agents() -> list[dict[str, object]]:
        return []

    async def fake_list_managed_vaults() -> list[dict[str, object]]:
        return []

    state.resources.ensure = fake_ensure  # type: ignore[method-assign]
    state.resources.ensure_all_agent_vaults = fake_ensure_all_agent_vaults  # type: ignore[method-assign]
    state.claude.deploy_agent = fake_deploy_agent  # type: ignore[method-assign]
    state.claude.list_managed_agents = fake_list_managed_agents  # type: ignore[method-assign]
    state.claude.list_managed_vaults = fake_list_managed_vaults  # type: ignore[method-assign]

    results = asyncio.run(state.deploy.apply())

    assert ensure_calls == [True]
    assert vault_calls == [True]
    assert deploy_calls
    assert results
    assert state.provider_state.get("claude_managed_agents", "agent", "researcher").external_id == "agent_remote_researcher"


def test_deployment_service_archives_removed_agents_and_vaults_from_state(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=False,
        )
    )
    state.config.agents = {"researcher": state.config.agents["researcher"].model_copy(deep=True)}

    state.provider_state.upsert(
        ProviderStateRecord(
            provider="claude_managed_agents",
            resource_type="agent",
            logical_key="obsolete",
            external_id="agent_obsolete",
            metadata={},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    state.provider_state.upsert(
        ProviderStateRecord(
            provider="claude_managed_agents",
            resource_type="vault",
            logical_key="vault:obsolete",
            external_id="vault_obsolete",
            metadata={},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    state.provider_state.upsert(
        ProviderStateRecord(
            provider="claude_managed_agents",
            resource_type="vault_credential",
            logical_key="vault_credential:obsolete:external_research",
            external_id="cred_obsolete",
            metadata={},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )

    archived_agents: list[str] = []
    archived_vaults: list[str] = []
    archived_credentials: list[tuple[str, str]] = []

    async def fake_ensure(*, verify_remote: bool = False) -> tuple[str, str]:
        return "env", "mem"

    async def fake_ensure_all_agent_vaults(*, verify_remote: bool = False) -> None:
        return None

    async def fake_deploy_agent(agent, system_prompt: str, *, existing_agent_id: str | None = None) -> dict[str, object]:
        return {"id": f"agent_remote_{agent.agent_id}", "name": agent.agent_id, "version": 1}

    async def fake_archive_agent(agent_id: str) -> None:
        archived_agents.append(agent_id)

    async def fake_list_managed_agents() -> list[dict[str, object]]:
        return []

    async def fake_list_managed_vaults() -> list[dict[str, object]]:
        return []

    async def fake_list_vault_credentials(vault_id: str) -> list[dict[str, object]]:
        return [{"id": "cred_obsolete"}] if vault_id == "vault_obsolete" else []

    async def fake_archive_vault_credential(vault_id: str, credential_id: str) -> None:
        archived_credentials.append((vault_id, credential_id))

    async def fake_archive_vault(vault_id: str) -> None:
        archived_vaults.append(vault_id)

    state.resources.ensure = fake_ensure  # type: ignore[method-assign]
    state.resources.ensure_all_agent_vaults = fake_ensure_all_agent_vaults  # type: ignore[method-assign]
    state.claude.deploy_agent = fake_deploy_agent  # type: ignore[method-assign]
    state.claude.archive_agent = fake_archive_agent  # type: ignore[method-assign]
    state.claude.list_managed_agents = fake_list_managed_agents  # type: ignore[method-assign]
    state.claude.list_managed_vaults = fake_list_managed_vaults  # type: ignore[method-assign]
    state.claude.list_vault_credentials = fake_list_vault_credentials  # type: ignore[method-assign]
    state.claude.archive_vault_credential = fake_archive_vault_credential  # type: ignore[method-assign]
    state.claude.archive_vault = fake_archive_vault  # type: ignore[method-assign]

    asyncio.run(state.deploy.apply())

    assert archived_agents == ["agent_obsolete"]
    assert archived_credentials == [("vault_obsolete", "cred_obsolete")]
    assert archived_vaults == ["vault_obsolete"]
    assert state.provider_state.get("claude_managed_agents", "agent", "obsolete") is None
    assert state.provider_state.get("claude_managed_agents", "vault", "vault:obsolete") is None
    assert state.provider_state.get("claude_managed_agents", "vault_credential", "vault_credential:obsolete:external_research") is None


def test_deployment_service_archives_remote_orphaned_agents(tmp_path) -> None:
    state = build_state(
        Settings(
            sqlite_path=str(tmp_path / "provider.db"),
            workspace_path="workspace",
            thruflow_fake_claude=False,
        )
    )
    state.config.agents = {"researcher": state.config.agents["researcher"].model_copy(deep=True)}

    archived_agents: list[str] = []

    async def fake_ensure(*, verify_remote: bool = False) -> tuple[str, str]:
        return "env", "mem"

    async def fake_ensure_all_agent_vaults(*, verify_remote: bool = False) -> None:
        return None

    async def fake_deploy_agent(agent, system_prompt: str, *, existing_agent_id: str | None = None) -> dict[str, object]:
        return {"id": f"agent_remote_{agent.agent_id}", "name": agent.agent_id, "version": 1}

    async def fake_list_managed_agents() -> list[dict[str, object]]:
        return [
            {"id": "agent_orphan", "metadata": {"managed_agents_repo": "thruflow", "managed_agents_slug": "obsolete"}},
            {"id": "agent_keep", "metadata": {"managed_agents_repo": "thruflow", "managed_agents_slug": "researcher"}},
        ]

    async def fake_archive_agent(agent_id: str) -> None:
        archived_agents.append(agent_id)

    async def fake_list_managed_vaults() -> list[dict[str, object]]:
        return []

    state.resources.ensure = fake_ensure  # type: ignore[method-assign]
    state.resources.ensure_all_agent_vaults = fake_ensure_all_agent_vaults  # type: ignore[method-assign]
    state.claude.deploy_agent = fake_deploy_agent  # type: ignore[method-assign]
    state.claude.list_managed_agents = fake_list_managed_agents  # type: ignore[method-assign]
    state.claude.archive_agent = fake_archive_agent  # type: ignore[method-assign]
    state.claude.list_managed_vaults = fake_list_managed_vaults  # type: ignore[method-assign]

    asyncio.run(state.deploy.apply())

    assert archived_agents == ["agent_orphan"]
