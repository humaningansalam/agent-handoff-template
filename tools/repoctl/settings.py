from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .io import RepoctlError

DEFAULT_DOCUMENT_LANGUAGE = "en"
SUPPORTED_DOCUMENT_LANGUAGES = {"en", "ko"}


def load_repoctl_settings(root: Path) -> dict[str, Any]:
    path = root / "docs/repoctl.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RepoctlError(f"invalid docs/repoctl.json: {exc.msg}", code="invalid_repoctl_settings", path="docs/repoctl.json") from exc
    if not isinstance(data, dict):
        raise RepoctlError("docs/repoctl.json must contain a JSON object", code="invalid_repoctl_settings", path="docs/repoctl.json")
    return data


def document_language(root: Path) -> str:
    settings = load_repoctl_settings(root)
    value = settings.get("document_language", DEFAULT_DOCUMENT_LANGUAGE)
    if not isinstance(value, str):
        raise RepoctlError("docs/repoctl.json document_language must be a string", code="invalid_document_language", path="docs/repoctl.json")
    language = value.strip().lower()
    validate_document_language(language, source="docs/repoctl.json document_language")
    return language


def validate_document_language(language: str, *, source: str = "document_language") -> None:
    if language not in SUPPORTED_DOCUMENT_LANGUAGES:
        supported = ", ".join(sorted(SUPPORTED_DOCUMENT_LANGUAGES))
        raise RepoctlError(f"unsupported {source}: {language}; supported values: {supported}", code="invalid_document_language")
