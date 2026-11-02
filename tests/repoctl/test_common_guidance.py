from pathlib import Path


COMMON_GUIDANCE = (
    "AGENTS.md",
    "docs/workflows/README.md",
    "docs/workflows/INDEX.md",
)

FORBIDDEN_HARNESS_TEXT = (
    "gpt-6-astra",
    "terra",
    "product execution handoff",
    "external completion",
    "expert director",
    "main handles product judgment",
    "same coordinator",
    "codex queue",
    "sessionplane collects",
    "hostspan waits",
)


def test_common_guidance_is_model_and_orchestrator_neutral() -> None:
    root = Path(__file__).resolve().parents[2]
    hits: list[str] = []

    for relative in COMMON_GUIDANCE:
        text = (root / relative).read_text(encoding="utf-8").lower()
        for forbidden in FORBIDDEN_HARNESS_TEXT:
            if forbidden in text:
                hits.append(f"{relative}: {forbidden}")

    assert not hits
    assert "belong to the adopting environment" in (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "does not prescribe agent roles" in (root / "docs/workflows/README.md").read_text(encoding="utf-8")
