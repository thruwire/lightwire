from __future__ import annotations

from app.models import NormalizedMessage, RouteConfig


def route_matches(message: NormalizedMessage, route: RouteConfig) -> bool:
    if not route.enabled:
        return False
    if route.match.source != message.source:
        return False
    if route.match.type != message.type:
        return False
    agent_id = route.match.agent_id
    if agent_id and message.metadata.get("agent_id") != agent_id:
        return False
    return True
