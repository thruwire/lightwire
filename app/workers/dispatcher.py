from __future__ import annotations

import logging

from app.connectors.slack import SlackConnector
from app.models import NormalizedMessage
from app.repositories.messages import MessageRepository
from app.repositories.sessions import SessionRepository
from app.routing.router import Router
from app.connectors.telegram import TelegramConnector
from app.workers.output_handler import OutputHandler
from app.workers.session_runner import SessionRunner


logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(
        self,
        messages: MessageRepository,
        sessions: SessionRepository,
        router: Router,
        runner: SessionRunner,
        output_handler: OutputHandler,
        telegram_connector: TelegramConnector | None = None,
        slack_connector: SlackConnector | None = None,
    ) -> None:
        self.messages = messages
        self.sessions = sessions
        self.router = router
        self.runner = runner
        self.output_handler = output_handler
        self.telegram_connector = telegram_connector
        self.slack_connector = slack_connector

    async def dispatch(self, message: NormalizedMessage) -> list[str]:
        # Every event is persisted before routing so downstream debugging can reconstruct the full correlation chain.
        self.messages.create(message)
        created_sessions: list[str] = []
        for route in self.router.resolve(message):
            session, output = await self.runner.run_dispatch(route)
            created_sessions.append(session.id)
            output_message = self.output_handler.to_message(output, parent_message_id=message.id)
            self.sessions.update_status(session.id, session.status, output_message_id=output_message.id)
            if not self._output_is_valid(output_message):
                continue
            await self._handle_reply(output_message, message)
            # Agent output re-enters the same dispatcher path, which gives ThruFlow simple DAG chaining without a graph engine.
            await self.dispatch(output_message)
        return created_sessions

    def _output_is_valid(self, output_message: NormalizedMessage) -> bool:
        return bool(output_message.metadata.get("output_valid", True))

    async def _handle_reply(self, output_message: NormalizedMessage, parent_message: NormalizedMessage) -> None:
        reply = output_message.metadata.get("reply")
        if not isinstance(reply, dict):
            return
        if reply.get("mode") != "final_output":
            return
        root_message = self._find_root_message(parent_message)
        if reply.get("connector") == "telegram":
            try:
                await self._reply_telegram(output_message, root_message)
            except Exception:
                logger.exception("Telegram reply delivery failed for message %s", output_message.id)
            return
        if reply.get("connector") == "slack":
            try:
                await self._reply_slack(output_message, root_message)
            except Exception:
                logger.exception("Slack reply delivery failed for message %s", output_message.id)

    async def _reply_telegram(self, output_message: NormalizedMessage, root_message: NormalizedMessage) -> None:
        if not self.telegram_connector or not self.telegram_connector.config.telegram.send_replies:
            return
        if root_message.source.value != "telegram":
            return
        chat_id = root_message.payload.get("chat_id")
        if not chat_id:
            return
        reply_to_message_id = root_message.payload.get("message_id")
        await self.telegram_connector.send_message(
            chat_id=str(chat_id),
            text=str(output_message.payload.get("content", "")),
            reply_to_message_id=reply_to_message_id if isinstance(reply_to_message_id, int) else None,
        )

    async def _reply_slack(self, output_message: NormalizedMessage, root_message: NormalizedMessage) -> None:
        if not self.slack_connector or not self.slack_connector.config.slack.send_replies:
            return
        if root_message.source.value != "slack":
            return
        channel = root_message.payload.get("channel")
        if not channel:
            return
        thread_ts = root_message.payload.get("thread_ts") or root_message.payload.get("ts")
        await self.slack_connector.send_message(
            channel=str(channel),
            text=self._truncate_reply(str(output_message.payload.get("summary") or output_message.payload.get("content", ""))),
            thread_ts=str(thread_ts) if thread_ts else None,
        )

    def _truncate_reply(self, text: str, max_length: int = 900) -> str:
        compact = " ".join(text.split())
        if len(compact) <= max_length:
            return compact
        return compact[: max_length - 3].rstrip() + "..."

    def _find_root_message(self, message: NormalizedMessage) -> NormalizedMessage:
        current = message
        while current.parent_message_id:
            # Walking parent links back to the root message is enough to recover connector-specific reply context.
            parent = self.messages.get(current.parent_message_id)
            if not parent:
                break
            current = parent
        return current
