from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.db.base import Database
from app.models import AgentOutputRecord, ConnectorCursorRecord, HeartbeatState, NormalizedMessage, ProviderStateRecord, SessionRecord, SessionStatus
from app.repositories.cursors import ConnectorCursorRepository
from app.repositories.heartbeats import HeartbeatRepository
from app.repositories.messages import MessageRepository
from app.repositories.outputs import AgentOutputRepository
from app.repositories.provider_state import ProviderStateRepository
from app.repositories.sessions import SessionRepository
from app.utils.time import utc_now


def _to_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def _from_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    return json.loads(value)


def _to_json_list(value: list[str]) -> str:
    return json.dumps(value)


def _from_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    return json.loads(value)


class SqliteDatabase(Database):
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self._connect() as conn:
            if self._has_legacy_column(conn, "sessions", "agent_key"):
                conn.execute("DROP TABLE IF EXISTS sessions")
            if self._has_legacy_column(conn, "agent_outputs", "agent_key"):
                conn.execute("DROP TABLE IF EXISTS agent_outputs")
            if not self._table_has_columns(
                conn,
                "agent_outputs",
                {"summary", "artifacts_json", "handoffs_json", "memory_paths_json"},
            ):
                conn.execute("DROP TABLE IF EXISTS agent_outputs")
            # Schema creation lives in one place so SQLite can be swapped later behind the repository interfaces.
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    parent_message_id TEXT,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    route_id TEXT,
                    agent_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    external_session_id TEXT,
                    memory_store_id TEXT NOT NULL,
                    output_message_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS heartbeats (
                    heartbeat_id TEXT PRIMARY KEY,
                    next_run_at TEXT NOT NULL,
                    last_run_at TEXT
                );

                CREATE TABLE IF NOT EXISTS agent_outputs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    artifacts_json TEXT NOT NULL,
                    handoffs_json TEXT NOT NULL,
                    memory_paths_json TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS connector_cursors (
                    connector TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    cursor TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (connector, scope)
                );

                CREATE TABLE IF NOT EXISTS provider_state (
                    provider TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    logical_key TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (provider, resource_type, logical_key)
                );
                """
            )

    def _has_legacy_column(self, conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(row["name"] == column_name for row in rows)

    def _table_has_columns(self, conn: sqlite3.Connection, table_name: str, columns: set[str]) -> bool:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        if not rows:
            return True
        existing = {row["name"] for row in rows}
        return columns.issubset(existing)

    def execute(self, query: str, params: Sequence[Any] = ()) -> None:
        with self._connect() as conn:
            conn.execute(query, params)
            conn.commit()

    def fetchone(self, query: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return dict(row) if row else None

    def fetchall(self, query: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


class SqliteMessageRepository(MessageRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, message: NormalizedMessage) -> None:
        # JSON columns keep the normalized event payload flexible while the orchestration model is still evolving.
        self.db.execute(
            """
            INSERT INTO messages (
                id, source, type, payload, correlation_id, parent_message_id, metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.id,
                message.source.value,
                message.type.value,
                _to_json(message.payload),
                message.correlation_id,
                message.parent_message_id,
                _to_json(message.metadata),
                message.created_at.isoformat(),
            ),
        )

    def get(self, message_id: str) -> NormalizedMessage | None:
        row = self.db.fetchone("SELECT * FROM messages WHERE id = ?", (message_id,))
        return NormalizedMessage.model_validate(
            {
                **row,
                "payload": _from_json(row["payload"]),
                "metadata": _from_json(row["metadata"]),
            }
        ) if row else None


class SqliteSessionRepository(SessionRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, session: SessionRecord) -> None:
        self.db.execute(
            """
            INSERT INTO sessions (
                id, route_id, agent_id, message_id, correlation_id, prompt, status,
                external_session_id, memory_store_id, output_message_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.route_id,
                session.agent_id,
                session.message_id,
                session.correlation_id,
                session.prompt,
                session.status.value,
                session.external_session_id,
                session.memory_store_id,
                session.output_message_id,
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
            ),
        )

    def get(self, session_id: str) -> SessionRecord | None:
        row = self.db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return SessionRecord.model_validate(row) if row else None

    def update_status(
        self,
        session_id: str,
        status: SessionStatus,
        external_session_id: str | None = None,
        output_message_id: str | None = None,
    ) -> None:
        current = self.get(session_id)
        if not current:
            return
        # Partial updates preserve previously recorded provider IDs and output links when only one field changes.
        self.db.execute(
            """
            UPDATE sessions
            SET status = ?, external_session_id = ?, output_message_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status.value,
                external_session_id or current.external_session_id,
                output_message_id or current.output_message_id,
                utc_now().isoformat(),
                session_id,
            ),
        )


class SqliteHeartbeatRepository(HeartbeatRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    def upsert(self, state: HeartbeatState) -> None:
        self.db.execute(
            """
            INSERT INTO heartbeats (heartbeat_id, next_run_at, last_run_at)
            VALUES (?, ?, ?)
            ON CONFLICT(heartbeat_id) DO UPDATE SET
              next_run_at = excluded.next_run_at,
              last_run_at = excluded.last_run_at
            """,
            (
                state.heartbeat_id,
                state.next_run_at.isoformat(),
                state.last_run_at.isoformat() if state.last_run_at else None,
            ),
        )

    def get(self, heartbeat_id: str) -> HeartbeatState | None:
        row = self.db.fetchone("SELECT * FROM heartbeats WHERE heartbeat_id = ?", (heartbeat_id,))
        return HeartbeatState.model_validate(row) if row else None

    def list_all(self) -> list[HeartbeatState]:
        return [HeartbeatState.model_validate(row) for row in self.db.fetchall("SELECT * FROM heartbeats")]


class SqliteAgentOutputRepository(AgentOutputRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, output: AgentOutputRecord) -> None:
        self.db.execute(
            """
            INSERT INTO agent_outputs (
                id, session_id, agent_id, correlation_id, content, summary,
                artifacts_json, handoffs_json, memory_paths_json, metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                output.id,
                output.session_id,
                output.agent_id,
                output.correlation_id,
                output.content,
                output.summary,
                _to_json_list(output.artifacts),
                _to_json_list(output.handoffs),
                _to_json_list(output.memory_paths),
                _to_json(output.metadata),
                output.created_at.isoformat(),
            ),
        )

    def get_by_session(self, session_id: str) -> AgentOutputRecord | None:
        row = self.db.fetchone("SELECT * FROM agent_outputs WHERE session_id = ?", (session_id,))
        return (
            AgentOutputRecord.model_validate(
                {
                    **row,
                    "artifacts": _from_json_list(row["artifacts_json"]),
                    "handoffs": _from_json_list(row["handoffs_json"]),
                    "memory_paths": _from_json_list(row["memory_paths_json"]),
                    "metadata": _from_json(row["metadata"]),
                }
            )
            if row
            else None
        )


class SqliteConnectorCursorRepository(ConnectorCursorRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, connector: str, scope: str) -> ConnectorCursorRecord | None:
        row = self.db.fetchone(
            "SELECT * FROM connector_cursors WHERE connector = ? AND scope = ?",
            (connector, scope),
        )
        return ConnectorCursorRecord.model_validate(row) if row else None

    def upsert(self, record: ConnectorCursorRecord) -> None:
        # Connectors rely on idempotent cursor writes so polling loops can safely resume after failures.
        self.db.execute(
            """
            INSERT INTO connector_cursors (connector, scope, cursor, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(connector, scope) DO UPDATE SET
              cursor = excluded.cursor,
              updated_at = excluded.updated_at
            """,
            (
                record.connector,
                record.scope,
                record.cursor,
                record.updated_at.isoformat(),
            ),
        )


class SqliteProviderStateRepository(ProviderStateRepository):
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, provider: str, resource_type: str, logical_key: str) -> ProviderStateRecord | None:
        row = self.db.fetchone(
            """
            SELECT * FROM provider_state
            WHERE provider = ? AND resource_type = ? AND logical_key = ?
            """,
            (provider, resource_type, logical_key),
        )
        return ProviderStateRecord.model_validate({**row, "metadata": _from_json(row["metadata"])}) if row else None

    def upsert(self, record: ProviderStateRecord) -> None:
        # Provider state is keyed by logical resource name, not raw ID, so recreated workspaces can reuse the same lookup path.
        self.db.execute(
            """
            INSERT INTO provider_state (
                provider, resource_type, logical_key, external_id, metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, resource_type, logical_key) DO UPDATE SET
              external_id = excluded.external_id,
              metadata = excluded.metadata,
              updated_at = excluded.updated_at
            """,
            (
                record.provider,
                record.resource_type,
                record.logical_key,
                record.external_id,
                _to_json(record.metadata),
                record.created_at.isoformat(),
                record.updated_at.isoformat(),
            ),
        )
