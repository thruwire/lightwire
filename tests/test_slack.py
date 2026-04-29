import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings, load_runtime_config
from app.connectors.slack import SlackConnector
from app.db.sqlite import SqliteConnectorCursorRepository, SqliteDatabase
from app.models import NormalizedMessage, SlackMode


class FakeSlackWebClient:
    def __init__(self, *, channel_map: dict[str, str] | None = None) -> None:
        self.messages: list[dict[str, object]] = []
        self.channel_map = channel_map or {"ai-playground": "C123456", "ops-private": "G999999"}

    async def auth_test(self) -> dict[str, object]:
        return {"ok": True, "user_id": "UBOT"}

    async def conversations_list(self, **kwargs) -> dict[str, object]:
        return {
            "ok": True,
            "channels": [{"name": name, "id": channel_id} for name, channel_id in self.channel_map.items()],
            "response_metadata": {"next_cursor": ""},
        }

    async def chat_postMessage(self, **kwargs) -> dict[str, object]:
        self.messages.append(kwargs)
        return {"ok": True}


class FailingSlackWebClient(FakeSlackWebClient):
    async def conversations_list(self, **kwargs) -> dict[str, object]:
        raise RuntimeError("missing_scope")


class FakeSocketClient:
    def __init__(self) -> None:
        self.socket_mode_request_listeners: list[object] = []
        self.responses: list[object] = []
        self.connected = False
        self.disconnected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def send_socket_mode_response(self, response: object) -> None:
        self.responses.append(response)


def _slack_event(
    *,
    event_type: str = "message",
    channel: str = "C123456",
    channel_type: str = "channel",
    user: str = "U123",
    text: str = "<@UBOT> hello",
    ts: str = "1710000000.000100",
    thread_ts: str | None = None,
    subtype: str | None = None,
    bot_id: str | None = None,
) -> dict:
    event = {
        "type": event_type,
        "channel": channel,
        "channel_type": channel_type,
        "user": user,
        "text": text,
        "ts": ts,
        "event_ts": ts,
    }
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    if subtype is not None:
        event["subtype"] = subtype
    if bot_id is not None:
        event["bot_id"] = bot_id
    return event


async def _noop_dispatch(_: NormalizedMessage) -> list[str]:
    return []


def _build_repo(tmp_path: Path) -> SqliteConnectorCursorRepository:
    db = SqliteDatabase(str(tmp_path / "slack.db"))
    db.initialize()
    return SqliteConnectorCursorRepository(db)


def _build_config(tmp_path: Path, **settings_overrides):
    settings = Settings(
        sqlite_path=str(tmp_path / "slack.db"),
        workspace_path="workspace",
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        **settings_overrides,
    )
    return load_runtime_config(settings)


def test_slack_config_loads_socket_mode_by_default(tmp_path) -> None:
    config = _build_config(tmp_path)
    assert config.slack.mode == SlackMode.SOCKET


def test_slack_config_defaults_to_socket_when_mode_missing(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "agents" / "researcher").mkdir(parents=True)
    (workspace / "skills").mkdir()
    (workspace / "prompt_templates").mkdir()
    (workspace / "agents" / "researcher" / "config.yaml").write_text(
        "agent_id: researcher\nenabled: true\nmemory:\n  access: read_write\n",
        encoding="utf-8",
    )
    (workspace / "agents" / "researcher" / "AGENT.md").write_text("You are the Researcher agent.", encoding="utf-8")
    (workspace / "tools.yaml").write_text("built_in: {}\nmcp_servers: {}\n", encoding="utf-8")
    (workspace / "routes.yaml").write_text("routes: []\n", encoding="utf-8")
    (workspace / "heartbeats.yaml").write_text("heartbeats: []\n", encoding="utf-8")
    (workspace / "slack.yaml").write_text(
        "enabled: true\nchannels:\n  - channel_id: \"C123456\"\nbehavior:\n  requires_mention: true\n  allow_dms: true\n  prefixes: []\nignore_bot_messages: true\n",
        encoding="utf-8",
    )
    config = load_runtime_config(
        Settings(sqlite_path=":memory:", workspace_path=str(workspace), slack_bot_token="xoxb-test", slack_app_token="xapp-test")
    )
    assert config.slack.mode == SlackMode.SOCKET


def test_missing_slack_app_token_raises_clear_error(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = load_runtime_config(
        Settings(sqlite_path=str(tmp_path / "slack.db"), workspace_path="workspace", slack_bot_token="xoxb-test")
    )
    connector = SlackConnector(config, repo, _noop_dispatch, web_client=FakeSlackWebClient(), socket_client=FakeSocketClient())
    with pytest.raises(RuntimeError, match="SLACK_APP_TOKEN"):
        asyncio.run(connector.start())


def test_channel_name_resolution_to_id(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    config.slack.channels = [config.slack.channels[1]]
    connector = SlackConnector(config, repo, _noop_dispatch, web_client=FakeSlackWebClient())
    asyncio.run(connector._prepare_runtime_state())
    assert config.slack.channels[0].channel_id == "C123456"
    assert connector._allowed_channel_ids == {"C123456"}


def test_unknown_channel_name_fails_fast(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    config.slack.channels = [config.slack.channels[1]]
    config.slack.channels[0].channel_name = "does-not-exist"
    connector = SlackConnector(config, repo, _noop_dispatch, web_client=FakeSlackWebClient(channel_map={"ai-playground": "C123456"}))
    with pytest.raises(RuntimeError, match="does-not-exist"):
        asyncio.run(connector.start())


def test_channel_name_resolution_uses_cache_before_calling_slack(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    config.slack.channels = [config.slack.channels[1]]
    cache_path = Path(config.settings.sqlite_path).resolve().parent / "slack_channels.json"
    cache_path.write_text('{"ai-playground": "C123456"}', encoding="utf-8")
    connector = SlackConnector(config, repo, _noop_dispatch, web_client=FailingSlackWebClient())
    asyncio.run(connector._prepare_runtime_state())
    assert connector._allowed_channel_ids == {"C123456"}


def test_channel_name_resolution_failure_is_clear(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    config.slack.channels = [config.slack.channels[1]]
    connector = SlackConnector(config, repo, _noop_dispatch, web_client=FailingSlackWebClient())
    with pytest.raises(RuntimeError, match="conversations.list"):
        asyncio.run(connector.start())


def test_multiple_channels_supported(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    config.slack.channels[1].channel_name = "ops-private"
    connector = SlackConnector(config, repo, _noop_dispatch, web_client=FakeSlackWebClient())
    asyncio.run(connector._prepare_runtime_state())
    assert connector._allowed_channel_ids == {"C123456", "G999999"}


def test_mention_required_behavior(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    connector = SlackConnector(config, repo, _noop_dispatch)
    connector._bot_user_id = "UBOT"
    connector._allowed_channel_ids = {"C123456"}
    assert connector.normalize_event(_slack_event(text="hello"), mode="socket") is None


def test_prefix_triggering(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    config.slack.behavior.prefixes = ["!tf"]
    connector = SlackConnector(config, repo, _noop_dispatch)
    connector._bot_user_id = "UBOT"
    connector._allowed_channel_ids = {"C123456"}
    message = connector.normalize_event(_slack_event(text="!tf summarize this"), mode="socket")
    assert message is not None
    assert message.payload["text"] == "summarize this"
    assert message.payload["matched_prefix"] == "!tf"


def test_dm_triggering(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    connector = SlackConnector(config, repo, _noop_dispatch)
    connector._bot_user_id = "UBOT"
    message = connector.normalize_event(_slack_event(channel="D123", channel_type="im"), mode="socket")
    assert message is not None
    assert message.payload["is_dm"] is True


def test_dm_disabled_behavior(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    config.slack.behavior.allow_dms = False
    connector = SlackConnector(config, repo, _noop_dispatch)
    connector._bot_user_id = "UBOT"
    assert connector.normalize_event(_slack_event(channel="D123", channel_type="im"), mode="socket") is None


def test_slack_bot_messages_ignored(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    connector = SlackConnector(config, repo, _noop_dispatch)
    connector._bot_user_id = "UBOT"
    connector._allowed_channel_ids = {"C123456"}
    assert connector.normalize_event(_slack_event(bot_id="B123"), mode="socket") is None


def test_thread_inclusion_exclusion(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    connector = SlackConnector(config, repo, _noop_dispatch)
    connector._bot_user_id = "UBOT"
    connector._allowed_channel_ids = {"C123456"}
    connector._channel_thread_policy = {"C123456": False}
    assert connector.normalize_event(_slack_event(thread_ts="1710000000.000200"), mode="socket") is None
    top_level = connector.normalize_event(_slack_event(), mode="socket")
    assert top_level is not None


def test_cleaned_text_removes_mention(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    connector = SlackConnector(config, repo, _noop_dispatch)
    connector._bot_user_id = "UBOT"
    connector._allowed_channel_ids = {"C123456"}
    message = connector.normalize_event(_slack_event(text="<@UBOT> summarize this"), mode="socket")
    assert message is not None
    assert message.payload["text"] == "summarize this"
    assert message.payload["mentioned_bot"] is True


def test_cleaned_text_removes_prefix(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    config.slack.behavior.prefixes = ["!tf"]
    connector = SlackConnector(config, repo, _noop_dispatch)
    connector._bot_user_id = "UBOT"
    connector._allowed_channel_ids = {"C123456"}
    message = connector.normalize_event(_slack_event(text="!tf   summarize this"), mode="socket")
    assert message is not None
    assert message.payload["text"] == "summarize this"


def test_duplicate_socket_event_ignored(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    dispatched: list[NormalizedMessage] = []

    async def dispatch(message: NormalizedMessage) -> list[str]:
        dispatched.append(message)
        return []

    connector = SlackConnector(config, repo, dispatch, web_client=FakeSlackWebClient(), socket_client=FakeSocketClient())
    request = SimpleNamespace(
        type="events_api",
        envelope_id="env-1",
        payload={"event_id": "Ev1", "event": _slack_event()},
    )

    async def run_test() -> None:
        await connector._prepare_runtime_state()
        await connector.handle_socket_request(request)
        await asyncio.sleep(0)
        await connector.handle_socket_request(request)
        await asyncio.sleep(0)

    asyncio.run(run_test())
    assert len(dispatched) == 1


def test_duplicate_socket_message_with_different_event_ids_ignored(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    dispatched: list[NormalizedMessage] = []

    async def dispatch(message: NormalizedMessage) -> list[str]:
        dispatched.append(message)
        return []

    connector = SlackConnector(config, repo, dispatch, web_client=FakeSlackWebClient(), socket_client=FakeSocketClient())
    request_one = SimpleNamespace(
        type="events_api",
        envelope_id="env-1",
        payload={"event_id": "Ev1", "event": _slack_event(event_type="app_mention")},
    )
    request_two = SimpleNamespace(
        type="events_api",
        envelope_id="env-2",
        payload={"event_id": "Ev2", "event": _slack_event(event_type="message")},
    )

    async def run_test() -> None:
        await connector._prepare_runtime_state()
        await connector.handle_socket_request(request_one)
        await asyncio.sleep(0)
        await connector.handle_socket_request(request_two)
        await asyncio.sleep(0)

    asyncio.run(run_test())
    assert len(dispatched) == 1


def test_socket_event_ack_happens_before_dispatch(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    web_client = FakeSlackWebClient()
    socket_client = FakeSocketClient()
    gate = asyncio.Event()
    dispatched: list[NormalizedMessage] = []

    async def dispatch(message: NormalizedMessage) -> list[str]:
        await gate.wait()
        dispatched.append(message)
        return []

    connector = SlackConnector(config, repo, dispatch, web_client=web_client, socket_client=socket_client)
    request = SimpleNamespace(
        type="events_api",
        envelope_id="env-1",
        payload={"event_id": "Ev1", "event": _slack_event()},
    )

    async def run_test() -> None:
        await connector._prepare_runtime_state()
        await connector.handle_socket_request(request)
        assert socket_client.responses
        assert dispatched == []
        gate.set()
        await asyncio.sleep(0)

    asyncio.run(run_test())
    assert len(dispatched) == 1


def test_slack_reply_uses_chat_post_message_with_channel_and_thread(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    web_client = FakeSlackWebClient()
    connector = SlackConnector(config, repo, _noop_dispatch, web_client=web_client)
    asyncio.run(connector.send_message("C123456", "done", thread_ts="1710000000.000100"))
    assert web_client.messages == [
        {
            "channel": "C123456",
            "text": "done",
            "mrkdwn": True,
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "done"}}],
            "thread_ts": "1710000000.000100",
        }
    ]


def test_slack_markdown_conversion(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    connector = SlackConnector(config, repo, _noop_dispatch)
    text = "# Title\n\n**bold** and [link](https://example.com)"
    assert connector._to_slack_mrkdwn(text) == "*Title*\n\n*bold* and <https://example.com|link>"


def test_slack_polling_cursor_logic(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    config.slack.mode = SlackMode.POLLING
    config.slack.behavior.prefixes = ["!tf"]
    config.slack.channels = [config.slack.channels[0]]

    def handler(request: httpx.Request) -> httpx.Response:
        if "conversations.history" in str(request.url):
            return httpx.Response(
                200,
                json={"messages": [{"ts": "2", "text": "!tf hello", "user": "U1", "channel_type": "channel"}]},
            )
        return httpx.Response(200, json={"messages": []})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://slack.com/api", transport=transport)
    connector = SlackConnector(config, repo, _noop_dispatch, polling_client=client, web_client=FakeSlackWebClient())
    messages = asyncio.run(connector.poll())
    assert len(messages) == 1
    assert messages[0].payload["text"] == "hello"
    assert repo.get("slack", "channel:C123456").cursor == "2"
    asyncio.run(client.aclose())
