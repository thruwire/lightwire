from __future__ import annotations

from app.models import NormalizedMessage
from app.repositories.messages import MessageRepository
from app.repositories.sessions import SessionRepository
from app.routing.router import Router
from app.connectors.telegram import TelegramConnector
from app.workers.output_handler import OutputHandler
from app.workers.session_runner import SessionRunner


class Dispatcher:
    def __init__(
        self,
        messages: MessageRepository,
        sessions: SessionRepository,
        router: Router,
        runner: SessionRunner,
        output_handler: OutputHandler,
        telegram_connector: TelegramConnector | None = None,
    ) -> None:
        self.messages = messages
        self.sessions = sessions
        self.router = router
        self.runner = runner
        self.output_handler = output_handler
        self.telegram_connector = telegram_connector

    async def dispatch(self, message: NormalizedMessage) -> list[str]:
        # Every event is persisted before routing so downstream debugging can reconstruct the full correlation chain.
        self.messages.create(message)
        created_sessions: list[str] = []
        for route in self.router.resolve(message):
            session, output = await self.runner.run_dispatch(route)
            created_sessions.append(session.id)
            output_message = self.output_handler.to_message(output, parent_message_id=message.id)
            self.sessions.update_status(session.id, session.status, output_message_id=output_message.id)
            await self._handle_reply(output_message, message)
            # Agent output re-enters the same dispatcher path, which gives ThruFlow simple DAG chaining without a graph engine.
            await self.dispatch(output_message)
        return created_sessions

    async def _handle_reply(self, output_message: NormalizedMessage, parent_message: NormalizedMessage) -> None:
        reply = output_message.metadata.get("reply")
        if not isinstance(reply, dict):
            return
        if reply.get("connector") != "telegram" or reply.get("mode") != "final_output":
            return
        if not self.telegram_connector or not self.telegram_connector.config.telegram.send_replies:
            return
        root_message = self._find_root_message(parent_message)
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

    def _find_root_message(self, message: NormalizedMessage) -> NormalizedMessage:
        current = message
        while current.parent_message_id:
            # Walking parent links back to the root message is enough to recover connector-specific reply context.
            parent = self.messages.get(current.parent_message_id)
            if not parent:
                break
            current = parent
        return current
