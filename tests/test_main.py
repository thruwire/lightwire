import asyncio
from types import SimpleNamespace

import app.main as main_module


class FakeService:
    def __init__(self, name: str, events: list[str], *, fail_start: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail_start = fail_start

    async def start(self) -> None:
        self.events.append(f"start:{self.name}")
        if self.fail_start:
            raise RuntimeError(f"failed to start {self.name}")

    async def stop(self) -> None:
        self.events.append(f"stop:{self.name}")


class FakeResources:
    def assert_runtime_ready(self) -> None:
        return None


def _state(name: str, events: list[str], *, fail_slack_start: bool = False):
    return SimpleNamespace(
        config=SimpleNamespace(settings=SimpleNamespace(name=name)),
        resources=FakeResources(),
        heartbeat_scheduler=FakeService(f"{name}:heartbeat", events),
        slack_connector=FakeService(f"{name}:slack", events, fail_start=fail_slack_start),
        telegram_connector=FakeService(f"{name}:telegram", events),
    )


def test_reload_replaces_running_background_services(monkeypatch) -> None:
    events: list[str] = []
    current = _state("old", events)
    replacement = _state("new", events)
    app = SimpleNamespace(state=SimpleNamespace(state=current))
    monkeypatch.setattr(main_module, "build_state", lambda settings: replacement)

    result = asyncio.run(main_module.reload_app_state(app))

    assert result is replacement
    assert app.state.state is replacement
    assert events == [
        "stop:old:telegram",
        "stop:old:slack",
        "stop:old:heartbeat",
        "start:new:heartbeat",
        "start:new:slack",
        "start:new:telegram",
    ]


def test_reload_restores_old_services_when_replacement_start_fails(monkeypatch) -> None:
    events: list[str] = []
    current = _state("old", events)
    replacement = _state("new", events, fail_slack_start=True)
    app = SimpleNamespace(state=SimpleNamespace(state=current))
    monkeypatch.setattr(main_module, "build_state", lambda settings: replacement)

    try:
        asyncio.run(main_module.reload_app_state(app))
    except RuntimeError as exc:
        assert str(exc) == "failed to start new:slack"
    else:
        raise AssertionError("reload should fail when a replacement service cannot start")

    assert app.state.state is current
    assert events[-3:] == ["start:old:heartbeat", "start:old:slack", "start:old:telegram"]
