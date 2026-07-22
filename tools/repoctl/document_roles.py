from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath


class DocumentRole(StrEnum):
    UNSPECIFIED = "unspecified"
    OPERATING_AUTHORITY = "operating_authority"
    PRODUCT_AUTHORITY = "product_authority"
    GOVERNANCE_AUTHORITY = "governance_authority"
    PROCEDURE = "procedure"
    REFERENCE = "reference"
    TEMPLATE = "template"
    GENERATED_VIEW = "generated_view"


AUTHORITY_DOCUMENT_ROLES = {
    DocumentRole.OPERATING_AUTHORITY,
    DocumentRole.PRODUCT_AUTHORITY,
    DocumentRole.GOVERNANCE_AUTHORITY,
}

ORDINARY_RECALL_EXCLUDED_DOCUMENT_ROLES = {
    DocumentRole.TEMPLATE,
}

SOURCE_EXCLUDED_DOCUMENT_ROLES = {
    DocumentRole.GENERATED_VIEW,
}


def classify_document_role(path: str, *, repository_path: str = "") -> DocumentRole:
    normalized = path.replace("\\", "/").strip("/").casefold()
    parts = PurePosixPath(normalized).parts
    repository_parts = PurePosixPath(repository_path.replace("\\", "/").strip("/").casefold()).parts
    if repository_parts and tuple(parts[: len(repository_parts)]) == repository_parts:
        parts = parts[len(repository_parts) :]
    local_path = "/".join(parts)
    name = parts[-1] if parts else ""

    if local_path.startswith("docs/knowledge/generated/"):
        return DocumentRole.GENERATED_VIEW
    if name == "template.md" and local_path.startswith("docs/"):
        return DocumentRole.TEMPLATE
    if local_path == "agents.md":
        return DocumentRole.OPERATING_AUTHORITY
    if local_path == "docs/board.md":
        return DocumentRole.REFERENCE
    if local_path == "docs/prd.md" or local_path.startswith("docs/prd/"):
        return DocumentRole.PRODUCT_AUTHORITY
    if local_path.startswith(("docs/adr/", "docs/contracts/")):
        return DocumentRole.GOVERNANCE_AUTHORITY
    if local_path.startswith("docs/workflows/"):
        if name in {"index.md", "readme.md"}:
            return DocumentRole.REFERENCE
        return DocumentRole.PROCEDURE
    if name.startswith("readme") or local_path.startswith("docs/"):
        return DocumentRole.REFERENCE
    return DocumentRole.UNSPECIFIED


def source_document_role(
    *,
    kind: str,
    path: str,
    repository_path: str = "",
    assigned: DocumentRole = DocumentRole.UNSPECIFIED,
) -> DocumentRole:
    if assigned != DocumentRole.UNSPECIFIED:
        return assigned
    if kind != "document":
        return DocumentRole.UNSPECIFIED
    return classify_document_role(path, repository_path=repository_path)
