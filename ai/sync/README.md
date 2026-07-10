# Adapter Rendering

## Procedure (re-render; no ad hoc edits)

Follow these steps when a role or agent definition needs to change:

1. **Update Canonical Source**: Edit the human-authored YAML files in `ai/roles/*.yaml`. This is the source of truth.
2. **Re-render Adapters**: Run `python3 tools/render_agent_adapters.py` to regenerate:
   - `.claude/agents/*.md`
   - `.codex/agents/*.toml`
   - `tools/registries/agent_registry.py`
   - `ai/generated-manifest.json`

3. **Verify Consistency**: Run `python3 tools/render_agent_adapters.py --check` and `./scripts/repoctl check --json`.

> [!WARNING]
> Never edit generated adapter files directly. Any ad-hoc changes will be overwritten during the next re-sync.

## Role Definitions

Canonical role definitions live in `ai/roles/`.

Render tool-specific adapters into:
- `.claude/agents/` (Markdown with YAML frontmatter)
- `.codex/agents/` (TOML)

## Current Status

`ai/roles/*.yaml` is the only role source. `.agents/skills/maintenance-workflow/SKILL.md` is the canonical maintenance skill source. Generated files must not be edited directly.

`repoctl check` uses only the standard library and compares raw source/output digests with `ai/generated-manifest.json`; it does not parse YAML or invoke uv. The renderer performs the semantic byte-for-byte regeneration check in development and CI.

Use `scripts/claude-maintenance` to activate `.claude/settings.maintenance.json`. Default Claude execution does not load maintenance-only agents, hooks, or the `repos/**` deny rules.
