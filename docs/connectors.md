# Connectors

ThruFlow connectors are responsible for:

- polling an external source
- converting source-specific payloads into `NormalizedMessage`
- persisting a cursor so polling can resume safely
- handing normalized messages to the dispatcher

## Slack

Slack uses the Web API and a polling model.

Main behavior:

- poll configured channels with `conversations.history`
- optionally poll thread roots with `conversations.replies`
- ignore bot messages by default
- store per-channel and per-thread cursors in SQLite
- convert new messages into normalized `source=slack` events

Slack config lives in `workspace/slack.yaml`.

## Telegram

Telegram uses the Bot API and a polling model.

Main behavior:

- poll `getUpdates`
- track a global `update_id` cursor in SQLite
- support plain incoming text messages in v1
- ignore unsupported update types
- ignore bot messages by default
- ignore chats not listed in `allowed_chats` when configured
- convert accepted updates into normalized `source=telegram` events

Telegram config lives in `workspace/telegram.yaml`.

Example normalized message payload:

```json
{
  "source": "telegram",
  "type": "message.created",
  "payload": {
    "chat_id": "123456789",
    "message_id": 123,
    "from_user_id": "777",
    "from_username": "alice",
    "text": "hello",
    "date": 1710000000
  },
  "metadata": {
    "connector": "telegram",
    "route_key": "default"
  }
}
```

## Reply Flow

Connectors only generate inbound messages by default. Telegram also supports optional outbound replies.

For v1, a route can declare:

```yaml
reply:
  connector: telegram
  mode: final_output
```

When the matching session completes:

1. ThruFlow normalizes the agent output.
2. The dispatcher walks the message parent chain back to the root event.
3. If the root message came from Telegram, the dispatcher sends the final output back with `sendMessage`.
4. When available, the original `message_id` is used as `reply_to_message_id`.

This keeps reply behavior attached to route intent rather than embedding it inside the provider adapter.

## Adding a New Connector

To add another polling connector, follow the same pattern as Slack and Telegram:

1. create a connector module under `app/connectors/`
2. define workspace config for polling and filtering
3. use `ConnectorCursorRepository` for idempotent resume
4. normalize source payloads into `NormalizedMessage`
5. dispatch through `Dispatcher.dispatch`
6. start and stop the connector from FastAPI lifespan
