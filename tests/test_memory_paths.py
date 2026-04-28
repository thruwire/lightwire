from app.memory.path_extractor import classify_memory_paths, extract_memory_paths
from app.models import AgentOutputRecord
from app.utils.time import utc_now
from app.workers.output_handler import OutputHandler


def test_extract_one_memory_path() -> None:
    paths = extract_memory_paths("Wrote artifact to /mnt/memory/artifacts/research/corr_1.md")
    assert paths == ["/mnt/memory/artifacts/research/corr_1.md"]


def test_extract_multiple_memory_paths() -> None:
    text = (
        "Artifact: /mnt/memory/artifacts/research/corr_1.md\n"
        "Handoff: /mnt/memory/handoffs/corr_1-research.md"
    )
    paths = extract_memory_paths(text)
    assert paths == [
        "/mnt/memory/artifacts/research/corr_1.md",
        "/mnt/memory/handoffs/corr_1-research.md",
    ]


def test_extract_strips_markdown_and_punctuation() -> None:
    text = (
        "I wrote `/mnt/memory/artifacts/research/corr_1.md`, "
        "and '/mnt/memory/handoffs/corr_1-research.md'."
    )
    paths = extract_memory_paths(text)
    assert paths == [
        "/mnt/memory/artifacts/research/corr_1.md",
        "/mnt/memory/handoffs/corr_1-research.md",
    ]


def test_extract_deduplicates_paths() -> None:
    text = (
        "/mnt/memory/artifacts/research/corr_1.md "
        "/mnt/memory/artifacts/research/corr_1.md"
    )
    paths = extract_memory_paths(text)
    assert paths == ["/mnt/memory/artifacts/research/corr_1.md"]


def test_classify_artifacts_handoffs_and_other_paths() -> None:
    extracted = classify_memory_paths(
        [
            "/mnt/memory/artifacts/research/corr_1.md",
            "/mnt/memory/handoffs/corr_1-research.md",
            "/mnt/memory/logs/corr_1.txt",
        ]
    )
    assert extracted.artifacts == ["/mnt/memory/artifacts/research/corr_1.md"]
    assert extracted.handoffs == ["/mnt/memory/handoffs/corr_1-research.md"]
    assert extracted.memory_paths == [
        "/mnt/memory/artifacts/research/corr_1.md",
        "/mnt/memory/handoffs/corr_1-research.md",
        "/mnt/memory/logs/corr_1.txt",
    ]


def test_output_handler_emits_artifacts_handoffs_and_memory_paths() -> None:
    output = AgentOutputRecord(
        id="out_1",
        session_id="sess_1",
        agent_id="researcher",
        correlation_id="corr_1",
        content=(
            "Research complete. I wrote /mnt/memory/artifacts/research/corr_1.md "
            "and /mnt/memory/handoffs/corr_1-research.md."
        ),
        summary="Research complete.",
        artifacts=["/mnt/memory/artifacts/research/corr_1.md"],
        handoffs=["/mnt/memory/handoffs/corr_1-research.md"],
        memory_paths=[
            "/mnt/memory/artifacts/research/corr_1.md",
            "/mnt/memory/handoffs/corr_1-research.md",
        ],
        metadata={"route_id": "api_to_research"},
        created_at=utc_now(),
    )
    message = OutputHandler().to_message(output, parent_message_id="msg_1")
    assert message.payload["artifacts"] == ["/mnt/memory/artifacts/research/corr_1.md"]
    assert message.payload["handoffs"] == ["/mnt/memory/handoffs/corr_1-research.md"]
    assert message.payload["memory_paths"] == [
        "/mnt/memory/artifacts/research/corr_1.md",
        "/mnt/memory/handoffs/corr_1-research.md",
    ]
