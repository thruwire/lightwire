import asyncio
from types import SimpleNamespace

import httpx

from app.config import Settings, load_runtime_config
from app.connectors.telegram import TelegramConnector
from app.db.sqlite import SqliteConnectorCursorRepository, SqliteDatabase
from app.main import build_state
from app.models import ConnectorCursorRecord, MessageSource, MessageType, NormalizedMessage
from app.utils.ids import new_id
from app.utils.time import utc_now


def _telegram_update(
    *,
    update_id: int = 10,
    chat_id: int = 123456789,
    message_id: int = 99,
    text: str = "hello",
    username: str = "alice",
    is_bot: bool = False,
) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": 1710000000,
            "text": text,
            "chat": {"id": chat_id},
            "from": {"id": 777, "username": username, "is_bot": is_bot},
        },
    }


async def _noop(_: object | None = None) -> list[str]:
    return []


def test_telegram_normalize_text_message(tmp_path) -> None:
    db = SqliteDatabase(str(tmp_path / "telegram.db"))
    db.initialize()
    repo = SqliteConnectorCursorRepository(db)
    config = load_runtime_config(
        Settings(sqlite_path=str(tmp_path / "telegram.db"), telegram_bot_token="token", workspace_path="workspace")
    )
    connector = TelegramConnector(config, repo, _noop)
    message = connector.normalize_update(_telegram_update())
    assert message is not None
    assert message.source == MessageSource.TELEGRAM
    assert message.type == MessageType.MESSAGE_CREATED
    assert message.payload["chat_id"] == "123456789"
    assert message.metadata["route_key"] == "default"


def test_telegram_ignores_unsupported_updates(tmp_path) -> None:
    db = SqliteDatabase(str(tmp_path / "telegram.db"))
    db.initialize()
    repo = SqliteConnectorCursorRepository(db)
    config = load_runtime_config(
        Settings(sqlite_path=str(tmp_path / "telegram.db"), telegram_bot_token="token", workspace_path="workspace")
    )
    connector = TelegramConnector(config, repo, _noop)
    assert connector.normalize_update({"update_id": 1, "edited_message": {"text": "ignored"}}) is None


def test_telegram_ignores_disallowed_chat_id(tmp_path) -> None:
    db = SqliteDatabase(str(tmp_path / "telegram.db"))
    db.initialize()
    repo = SqliteConnectorCursorRepository(db)
    config = load_runtime_config(
        Settings(sqlite_path=str(tmp_path / "telegram.db"), telegram_bot_token="token", workspace_path="workspace")
    )
    connector = TelegramConnector(config, repo, _noop)
    assert connector.normalize_update(_telegram_update(chat_id=555)) is None


def test_telegram_poll_once_dispatches_normalized_messages(tmp_path) -> None:
    db = SqliteDatabase(str(tmp_path / "telegram.db"))
    db.initialize()
    repo = SqliteConnectorCursorRepository(db)
    config = load_runtime_config(
        Settings(sqlite_path=str(tmp_path / "telegram.db"), telegram_bot_token="token", workspace_path="workspace")
    )
    repo.upsert(
        ConnectorCursorRecord(
            connector="telegram",
            scope="global",
            cursor="0",
            updated_at=utc_now(),
        )
    )
    dispatched: list[NormalizedMessage] = []

    async def dispatch(message: NormalizedMessage) -> list[str]:
        dispatched.append(message)
        return []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/bottoken/getUpdates"
        assert request.url.params["offset"] == "1"
        return httpx.Response(200, json={"ok": True, "result": [_telegram_update(update_id=1)]})

    client = httpx.AsyncClient(base_url="https://api.telegram.org", transport=httpx.MockTransport(handler))
    connector = TelegramConnector(config, repo, dispatch, client=client)
    messages = asyncio.run(connector.poll_once())
    assert len(messages) == 1
    assert len(dispatched) == 1
    assert repo.get("telegram", "global").cursor == "1"
    asyncio.run(client.aclose())


def test_telegram_send_message_uses_bot_api_endpoint(tmp_path) -> None:
    db = SqliteDatabase(str(tmp_path / "telegram.db"))
    db.initialize()
    repo = SqliteConnectorCursorRepository(db)
    config = load_runtime_config(
        Settings(sqlite_path=str(tmp_path / "telegram.db"), telegram_bot_token="token", workspace_path="workspace")
    )
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["json"] = request.read().decode("utf-8")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = httpx.AsyncClient(base_url="https://api.telegram.org", transport=httpx.MockTransport(handler))
    connector = TelegramConnector(config, repo, _noop, client=client)
    response = asyncio.run(connector.send_message("123456789", "done", reply_to_message_id=99))
    assert seen["path"] == "/bottoken/sendMessage"
    assert '"chat_id":"123456789"' in str(seen["json"])
    assert '"reply_to_message_id":99' in str(seen["json"])
    assert response["ok"] is True
    asyncio.run(client.aclose())


def test_final_output_reply_flow_uses_telegram_connector(tmp_path) -> None:
    state = build_state(
        Settings(sqlite_path=str(tmp_path / "flow.db"), workspace_path="workspace", lightwire_fake_claude=True)
    )
    sent_messages: list[dict[str, object]] = []

    class FakeTelegramConnector:
        def __init__(self) -> None:
            self.config = SimpleNamespace(telegram=state.config.telegram)

        async def send_message(self, chat_id: str, text: str, reply_to_message_id: int | None = None) -> dict[str, object]:
            sent_messages.append(
                {"chat_id": chat_id, "text": text, "reply_to_message_id": reply_to_message_id}
            )
            return {"ok": True}

    state.dispatcher.telegram_connector = FakeTelegramConnector()
    message = NormalizedMessage(
        id=new_id("msg"),
        source=MessageSource.TELEGRAM,
        type=MessageType.MESSAGE_CREATED,
        payload={
            "chat_id": "123456789",
            "message_id": 99,
            "from_user_id": "777",
            "from_username": "alice",
            "text": "What are the tradeoffs of using shared memory stores for agent handoffs?",
            "date": 1710000000,
        },
        correlation_id=new_id("corr"),
        parent_message_id=None,
        metadata={"connector": "telegram", "route_key": "default"},
        created_at=utc_now(),
    )
    session_ids = asyncio.run(state.dispatcher.dispatch(message))
    assert len(session_ids) == 1
    assert len(sent_messages) == 1
    assert sent_messages[0]["chat_id"] == "123456789"
    assert sent_messages[0]["reply_to_message_id"] == 99
    assert "Executive brief" in str(sent_messages[0]["text"])
