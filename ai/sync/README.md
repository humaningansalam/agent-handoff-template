# Adapter Rendering

## Procedure (re-render; no ad hoc edits)

Follow these steps when a role or agent definition needs to change:

1. **Update Canonical Source**: Edit the human-authored YAML files in `ai/roles/*.yaml`. This is the source of truth.
2. **Re-render Adapters**: Use the synchronization script (if available) or manually update the following files to match the canonical YAML:
   - `.claude/agents/*.md`
   - `.codex/agents/*.toml`

3. **Verify Consistency**: Ensure no tool-specific logic has drifted from the canonical role definition.

> [!WARNING]
> Never edit generated adapter files directly. Any ad-hoc changes will be overwritten during the next re-sync.

## Role Definitions

Canonical role definitions live in `ai/roles/`.

Render tool-specific adapters into:
- `.claude/agents/` (Markdown with YAML frontmatter)
- `.codex/agents/` (TOML)

## Current Status

No automated renderer exists yet.
Update generated adapters manually and keep their structure aligned with `ai/roles/README.md`.

## Future

When an automated renderer is needed, add it here as `render_roles.py` or equivalent.
Trigger: when role definitions change frequently enough that manual sync becomes error-prone.
