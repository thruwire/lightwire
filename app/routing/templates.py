from __future__ import annotations

from collections.abc import Mapping

from jinja2 import Environment, StrictUndefined


_env = Environment(undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True)


def render_template(template: str, context: Mapping[str, object]) -> str:
    # StrictUndefined makes missing route variables fail loudly instead of silently producing bad prompts.
    return _env.from_string(template).render(**context).strip()
