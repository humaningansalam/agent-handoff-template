# repoctl Upgrade Workflow

Use this workflow to update the workspace control plane in an adopting workspace without overwriting project state.

## Scope

`repoctl upgrade` updates only manifest-managed control-plane files such as `scripts/repoctl`, `tools/repoctl/**`, task templates, contracts, and canonical workflow docs.

It must preserve project state:

- `repos/**`
- `docs/BOARD.md`
- `docs/PRD.md`
- `docs/tasks/T-*.md`
- `docs/tasks/.repoctl-state/**`
- `docs/archive/tasks/**`
- project-specific workflow docs

## Flow

1. Obtain a repoctl release checkout or extracted release artifact.
2. Generate a dry-run plan:
   `./scripts/repoctl upgrade plan --from /path/to/release --output /tmp/repoctl-upgrade-plan.json --json`
3. Inspect `operations`, `preserve_paths`, and `conflicts`.
4. Apply only the inspected plan:
   `./scripts/repoctl upgrade apply --plan-file /tmp/repoctl-upgrade-plan.json --json`
5. Run verification:
   `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/repoctl tests/maintenance`
   `./scripts/repoctl check --json`
   `./scripts/repoctl meta check --json`

## Manifest Policy

- `replace_paths` are managed control-plane files that may be replaced from the release.
- `create_paths` are canonical docs/examples that are copied only when missing.
- `preserve_paths` are adopter-owned state and must not be overwritten.

Workflow docs are distributed as `create_paths` by default. This lets new workspaces receive the canonical workflows while preserving modified workflows in existing workspaces.

## Forbidden Shortcuts

- Do not parse Backlog, PRD, task, or workflow prose to infer upgrade scope.
- Do not repair Board, task, archive, or metadata state inside upgrade apply.
- Do not use broad mirror sync or delete files absent from the release artifact.
- Do not update `repos/**` through this command.
