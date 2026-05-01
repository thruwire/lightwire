from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.main import build_state
from app.models import MessageSource, MessageType, NormalizedMessage
from app.utils.ids import new_id
from app.utils.time import utc_now


async def main() -> None:
    settings = Settings(lightwire_fake_claude=True)
    state = build_state(settings)
    message = NormalizedMessage(
        id=new_id("msg"),
        source=MessageSource.API,
        type=MessageType.MESSAGE_CREATED,
        payload={
            "text": "What are the tradeoffs of using shared memory stores for agent handoffs?"
        },
        correlation_id=new_id("corr"),
        parent_message_id=None,
        metadata={},
        created_at=utc_now(),
    )
    session_ids = await state.dispatcher.dispatch(message)
    sessions = [
        session.model_dump(mode="json")
        for session in (
            state.sessions.get(row["id"])
            for row in state.db.fetchall("SELECT id FROM sessions WHERE correlation_id = ? ORDER BY created_at", (message.correlation_id,))
        )
        if session
    ]
    output = {
        "message_id": message.id,
        "correlation_id": message.correlation_id,
        "initial_sessions": session_ids,
        "sessions": sessions,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
