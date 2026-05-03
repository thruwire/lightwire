from __future__ import annotations

import uvicorn

from app.utils.logging import build_uvicorn_log_config


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        log_config=build_uvicorn_log_config(),
    )


if __name__ == "__main__":
    main()
