from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException

from app.claude.client import ClaudeManagedAgentClient
from app.claude.resources import ClaudeProviderResourceService
from app.claude.sessions import ClaudeSessionService
from app.config import RuntimeConfig, Settings, load_runtime_config
from app.connectors.slack import SlackConnector
from app.connectors.telegram import TelegramConnector
from app.db.sqlite import (
    SqliteAgentOutputRepository,
    SqliteConnectorCursorRepository,
    SqliteDatabase,
    SqliteHeartbeatRepository,
    SqliteMessageRepository,
    SqliteProviderStateRepository,
    SqliteSessionRepository,
)
from app.models import AppEventIn, HealthResponse, MessageSource, MessageType, NormalizedMessage
from app.routing.router import Router
from app.scheduler.heartbeat import HeartbeatScheduler
from app.utils.ids import new_id
from app.utils.logging import configure_logging
from app.utils.time import utc_now
from app.workers.dispatcher import Dispatcher
from app.workers.output_handler import OutputHandler
from app.workers.session_runner import SessionRunner


@dataclass
class AppState:
    # AppState centralizes long-lived services so request handlers and background tasks use one wiring graph.
    config: RuntimeConfig
    db: SqliteDatabase
    messages: SqliteMessageRepository
    sessions: SqliteSessionRepository
    heartbeats: SqliteHeartbeatRepository
    outputs: SqliteAgentOutputRepository
    cursors: SqliteConnectorCursorRepository
    provider_state: SqliteProviderStateRepository
    router: Router
    claude: ClaudeManagedAgentClient
    resources: ClaudeProviderResourceService
    dispatcher: Dispatcher
    heartbeat_scheduler: HeartbeatScheduler
    slack_connector: SlackConnector
    telegram_connector: TelegramConnector


def get_state(app: FastAPI) -> AppState:
    current_state = getattr(app.state, "state", None)
    if current_state is None:
        current_state = build_state()
        app.state.state = current_state
    return current_state


def build_state(settings: Settings | None = None) -> AppState:
    config = load_runtime_config(settings)
    db = SqliteDatabase(config.settings.sqlite_path)
    db.initialize()
    messages = SqliteMessageRepository(db)
    sessions = SqliteSessionRepository(db)
    heartbeats = SqliteHeartbeatRepository(db)
    outputs = SqliteAgentOutputRepository(db)
    cursors = SqliteConnectorCursorRepository(db)
    provider_state = SqliteProviderStateRepository(db)
    router = Router(config)
    claude = ClaudeManagedAgentClient(config)
    resources = ClaudeProviderResourceService(config, provider_state, claude)
    session_service = ClaudeSessionService(claude)
    output_handler = OutputHandler()
    dispatcher = Dispatcher(
        messages=messages,
        sessions=sessions,
        router=router,
        runner=SessionRunner(config, resources, session_service, sessions, outputs, output_handler),
        output_handler=output_handler,
    )
    slack_connector = SlackConnector(config, cursors, dispatcher.dispatch)
    telegram_connector = TelegramConnector(config, cursors, dispatcher.dispatch)
    # The dispatcher owns reply logic, so it needs connector handles after construction.
    dispatcher.slack_connector = slack_connector
    dispatcher.telegram_connector = telegram_connector
    heartbeat_scheduler = HeartbeatScheduler(config, heartbeats, dispatcher)
    return AppState(
        config=config,
        db=db,
        messages=messages,
        sessions=sessions,
        heartbeats=heartbeats,
        outputs=outputs,
        cursors=cursors,
        provider_state=provider_state,
        router=router,
        claude=claude,
        resources=resources,
        dispatcher=dispatcher,
        heartbeat_scheduler=heartbeat_scheduler,
        slack_connector=slack_connector,
        telegram_connector=telegram_connector,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    state = get_state(app)
    # Provider resources are cached in SQLite so startup can reuse them instead of creating duplicates.
    await state.resources.ensure()
    await state.resources.ensure_all_agent_vaults()
    await state.heartbeat_scheduler.start()
    await state.slack_connector.start()
    await state.telegram_connector.start()
    try:
        yield
    finally:
        await state.heartbeat_scheduler.stop()
        await state.slack_connector.stop()
        await state.telegram_connector.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="ThruFlow", lifespan=lifespan)

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        return HealthResponse()

    @app.post("/events")
    async def post_event(event: AppEventIn) -> dict[str, object]:
        state = get_state(app)
        # API events are normalized the same way connector events are, which keeps routing source-agnostic.
        message = NormalizedMessage(
            id=new_id("msg"),
            source=event.source,
            type=event.type,
            payload=event.payload,
            correlation_id=event.correlation_id or new_id("corr"),
            parent_message_id=event.parent_message_id,
            metadata=event.metadata,
            created_at=utc_now(),
        )
        session_ids = await state.dispatcher.dispatch(message)
        return {"message_id": message.id, "session_ids": session_ids, "correlation_id": message.correlation_id}

    @app.post("/heartbeats/{heartbeat_id}/run")
    async def run_heartbeat(heartbeat_id: str) -> dict[str, object]:
        state = get_state(app)
        try:
            session_ids = await state.heartbeat_scheduler.trigger(heartbeat_id)
        except StopIteration as exc:
            raise HTTPException(status_code=404, detail="heartbeat not found") from exc
        return {"heartbeat_id": heartbeat_id, "session_ids": session_ids}

    @app.get("/messages/{message_id}")
    async def get_message(message_id: str) -> dict[str, object]:
        state = get_state(app)
        message = state.messages.get(message_id)
        if not message:
            raise HTTPException(status_code=404, detail="message not found")
        return message.model_dump(mode="json")

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, object]:
        state = get_state(app)
        session = state.sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="session not found")
        return session.model_dump(mode="json")

    @app.get("/routes")
    async def get_routes() -> list[dict[str, object]]:
        state = get_state(app)
        return state.router.list_routes()

    @app.get("/heartbeats")
    async def get_heartbeats() -> list[dict[str, object]]:
        state = get_state(app)
        return state.router.list_heartbeats()

    @app.post("/admin/reload-config")
    async def reload_config() -> dict[str, str]:
        current_state = get_state(app)
        settings = current_state.config.settings if current_state else None
        state = build_state(settings)
        app.state.state = state
        await state.resources.ensure()
        await state.resources.ensure_all_agent_vaults()
        state.heartbeat_scheduler.initialize()
        return {"status": "reloaded"}

    return app


app = create_app()
