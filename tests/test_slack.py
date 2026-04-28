import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings, load_runtime_config
from app.connectors.slack import SlackConnector
from app.db.sqlite import SqliteConnectorCursorRepository, SqliteDatabase
from app.models import ConnectorCursorRecord, MessageSource, MessageType, NormalizedMessage, SlackMode
from app.utils.time import utc_now


class FakeSlackWebClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def chat_postMessage(self, **kwargs) -> dict[str, object]:
        self.messages.append(kwargs)
        return {"ok": True}


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
    channel: str = "C123456",
    user: str = "U123",
    text: str = "hello",
    ts: str = "1710000000.000100",
    thread_ts: str | None = None,
    subtype: str | None = None,
    bot_id: str | None = None,
) -> dict:
    event = {
        "type": "message",
        "channel": channel,
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


def _build_config(tmp_path: Path, **settings_overrides) -> object:
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
        "enabled: true\nchannels:\n  - channel_id: \"C123456\"\n    include_threads: true\nignore_bot_messages: true\n",
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


def test_slack_normal_message_event_normalization(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    connector = SlackConnector(config, repo, _noop_dispatch)
    message = connector.normalize_event(_slack_event(thread_ts="1710000000.000100"), mode="socket", event_id="Ev1")
    assert message is not None
    assert message.source == MessageSource.SLACK
    assert message.type == MessageType.MESSAGE_CREATED
    assert message.payload["channel"] == "C123456"
    assert message.payload["thread_ts"] == "1710000000.000100"
    assert message.metadata["mode"] == "socket"


def test_slack_bot_messages_ignored(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    connector = SlackConnector(config, repo, _noop_dispatch)
    assert connector.normalize_event(_slack_event(bot_id="B123"), mode="socket") is None


def test_slack_unsupported_subtypes_ignored(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    connector = SlackConnector(config, repo, _noop_dispatch)
    assert connector.normalize_event(_slack_event(subtype="message_changed"), mode="socket") is None


def test_slack_channel_allowlist_respected(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    connector = SlackConnector(config, repo, _noop_dispatch)
    assert connector.normalize_event(_slack_event(channel="C999999"), mode="socket") is None


def test_slack_include_threads_behavior(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    config.slack.channels[0].include_threads = False
    connector = SlackConnector(config, repo, _noop_dispatch)
    assert connector.normalize_event(_slack_event(thread_ts="1710000000.000100"), mode="socket") is None
    top_level = connector.normalize_event(_slack_event(), mode="socket")
    assert top_level is not None


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
    asyncio.run(connector.handle_socket_request(request))
    asyncio.run(asyncio.sleep(0))
    asyncio.run(connector.handle_socket_request(request))
    asyncio.run(asyncio.sleep(0))
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
        {"channel": "C123456", "text": "done", "thread_ts": "1710000000.000100"}
    ]


def test_slack_polling_cursor_logic(tmp_path) -> None:
    repo = _build_repo(tmp_path)
    config = _build_config(tmp_path)
    config.slack.mode = SlackMode.POLLING

    def handler(request: httpx.Request) -> httpx.Response:
        if "conversations.history" in str(request.url):
            return httpx.Response(200, json={"messages": [{"ts": "2", "text": "hello", "user": "U1"}]})
        return httpx.Response(200, json={"messages": []})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://slack.com/api", transport=transport)
    connector = SlackConnector(config, repo, _noop_dispatch, polling_client=client)
    messages = asyncio.run(connector.poll())
    assert len(messages) == 1
    assert repo.get("slack", "channel:C123456").cursor == "2"
    asyncio.run(client.aclose())
