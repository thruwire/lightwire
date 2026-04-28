import httpx

from app.config import Settings, load_runtime_config
from app.connectors.slack import SlackPoller
from app.db.sqlite import SqliteConnectorCursorRepository, SqliteDatabase


def test_slack_polling_cursor_logic(tmp_path) -> None:
    db = SqliteDatabase(str(tmp_path / "slack.db"))
    db.initialize()
    repo = SqliteConnectorCursorRepository(db)
    settings = Settings(sqlite_path=str(tmp_path / "slack.db"), slack_bot_token="token", workspace_path="workspace")
    config = load_runtime_config(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        if "conversations.history" in str(request.url):
            return httpx.Response(200, json={"messages": [{"ts": "2", "text": "hello", "user": "U1"}]})
        return httpx.Response(200, json={"messages": []})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(base_url="https://slack.com/api", transport=transport)
    poller = SlackPoller(config, repo, client=client)
    messages = __import__("asyncio").run(poller.poll())
    assert len(messages) == 1
    assert repo.get("slack", "channel:C123456").cursor == "2"
