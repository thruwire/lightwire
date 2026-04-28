from __future__ import annotations

import re

from app.models import ExtractedMemoryPaths


_MEMORY_PATH_PATTERN = re.compile(r"/mnt/memory/[^\s]+")
_TRAILING_STRIP_CHARS = ".,)]}\"'`"


def extract_memory_paths(text: str) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for match in _MEMORY_PATH_PATTERN.findall(text):
        path = match.rstrip(_TRAILING_STRIP_CHARS)
        if not path.startswith("/mnt/memory/"):
            continue
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def classify_memory_paths(paths: list[str]) -> ExtractedMemoryPaths:
    artifacts: list[str] = []
    handoffs: list[str] = []
    ordered: list[str] = []
    seen: set[str] = set()

    for path in paths:
        if not path.startswith("/mnt/memory/"):
            continue
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
        if "/artifacts/" in path:
            artifacts.append(path)
        elif "/handoffs/" in path:
            handoffs.append(path)

    return ExtractedMemoryPaths(artifacts=artifacts, handoffs=handoffs, memory_paths=ordered)
