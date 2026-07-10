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
3. Inspect `operations`, `state_migrations`, `preserve_paths`, and `conflicts`.
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

Task state is the only preserved state that upgrade may transform. Only state belonging to a live `todo`, `doing`, or `blocked` task is considered for migration. State left by done, canceled, archived, or missing tasks is preserved byte-for-byte because completed tasks never reuse that baseline. A readable known live schema is migrated atomically in place only when its original HEAD and required baseline fingerprints are already present. Upgrade does not infer missing history from the current HEAD or working tree. An unverifiable live state is preserved and reported as `task_state_migration_deferred`; it does not block managed control-plane updates, but `repoctl check`, task doctor, and lifecycle commands keep that task blocked until the state is resolved. There is no separate legacy store or compatibility runtime.

Known completion receipt schemas may also be migrated atomically in place. A schema v1 receipt is converted only when its recorded task artifact still exists and matches the recorded content hash. Its recorded changed paths are preserved, while repo evidence becomes `mode: none` and `attribution: none` because v1 did not store a deterministic diff fingerprint. Verification hashes are derived from the matching archived task section. Receipts whose artifact no longer matches remain untouched and continue to be reported as invalid isolated evidence.

Each apply receipt records the managed source digest and backup tree digest. `./scripts/repoctl upgrade status --json` calculates backup `availability` as `available`, `missing`, `digest_mismatch`, or `not_required` without modifying the receipt. Backups use manual retention in this version; there is no prune command.

Workflow docs are distributed as `create_paths` by default. This lets new workspaces receive the canonical workflows while preserving modified workflows in existing workspaces.

## Forbidden Shortcuts

- Do not parse Backlog, PRD, task, or workflow prose to infer upgrade scope.
- Do not repair Board, task Markdown, archive, or metadata state inside upgrade apply.
- Do not use broad mirror sync or delete files absent from the release artifact.
- Do not update `repos/**` through this command.
