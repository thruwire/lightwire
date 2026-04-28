from __future__ import annotations

import logging


def configure_logging() -> None:
    # A single plain-text format keeps container logs readable without requiring extra log infrastructure.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
