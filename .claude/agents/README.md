# Claude Agents (Generated)

These files are **generated adapters** for Claude.

> [!CAUTION]
> **Generated (re-render; no ad hoc edits)**
> Do not edit these files directly. Update the canonical role definitions in `ai/roles/*.yaml` first, then re-render these adapters. See `ai/sync/README.md` for the procedure.

## Format

Claude subagent files use Markdown with YAML frontmatter:

```markdown
---
name: reviewer
description: Read-only PR reviewer
tools:
  - Read
  - Grep
  - Glob
---

Review code like an owner.
Prioritize correctness, security, behavior regressions, and missing test coverage.
```

See `ai/roles/README.md` for the canonical role definitions.
