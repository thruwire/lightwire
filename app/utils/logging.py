from __future__ import annotations

import copy
import logging
from typing import Any

from uvicorn.config import LOGGING_CONFIG as UVICORN_LOGGING_CONFIG


def configure_logging() -> None:
    # A single plain-text format keeps container logs readable without requiring extra log infrastructure.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )


def build_uvicorn_log_config() -> dict[str, Any]:
    # Cloud Logging treats stderr as error severity, so send normal server logs to stdout.
    config = copy.deepcopy(UVICORN_LOGGING_CONFIG)
    config["handlers"]["default"]["stream"] = "ext://sys.stdout"
    return config
