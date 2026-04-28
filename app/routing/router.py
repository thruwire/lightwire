from __future__ import annotations

from app.config import RuntimeConfig
from app.models import HeartbeatConfig, NormalizedMessage, RouteDispatch
from app.routing.rules import route_matches
from app.routing.templates import render_template


class Router:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

    def list_routes(self) -> list[dict[str, object]]:
        return [route.model_dump(mode="json") for route in self.config.routes.routes]

    def list_heartbeats(self) -> list[dict[str, object]]:
        return [heartbeat.model_dump(mode="json") for heartbeat in self.config.heartbeats.heartbeats]

    def _render_template(self, template_ref: str, message: NormalizedMessage) -> str:
        template_text = self.config.get_prompt_template_text(template_ref)
        # Route templates only see normalized message data, which keeps connectors interchangeable at the routing layer.
        return render_template(
            template_text,
            {
                "message": message.model_dump(mode="json"),
                "payload": message.payload,
                "metadata": message.metadata,
                "correlation_id": message.correlation_id,
                "parent_message_id": message.parent_message_id,
            },
        )

    def _heartbeat_dispatch(self, message: NormalizedMessage) -> list[RouteDispatch]:
        heartbeat_id = message.metadata.get("heartbeat_id")
        heartbeat = next((item for item in self.config.heartbeats.heartbeats if item.id == heartbeat_id and item.enabled), None)
        if not heartbeat:
            return []
        return [self._dispatch_from_heartbeat(message, heartbeat)]

    def _dispatch_from_heartbeat(self, message: NormalizedMessage, heartbeat: HeartbeatConfig) -> RouteDispatch:
        return RouteDispatch(
            route_id=f"heartbeat:{heartbeat.id}",
            agent_id=heartbeat.target.agent_id,
            prompt=self._render_template(heartbeat.target.prompt_template, message),
            parent_message=message,
            reply=None,
            require_artifacts=False,
        )

    def resolve(self, message: NormalizedMessage) -> list[RouteDispatch]:
        dispatches: list[RouteDispatch] = []
        for route in self.config.routes.routes:
            if not route_matches(message, route):
                continue
            dispatches.append(
                RouteDispatch(
                    route_id=route.id,
                    agent_id=route.target.agent_id,
                    prompt=self._render_template(route.target.prompt_template, message),
                    parent_message=message,
                    reply=route.reply,
                    require_artifacts=route.require_artifacts,
                )
            )
        if message.source.value == "heartbeat":
            dispatches.extend(self._heartbeat_dispatch(message))
        return dispatches
