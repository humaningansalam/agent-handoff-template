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
2. Run the release artifact's updater against the adopter workspace. This avoids executing an older adopter runtime across the self-update boundary:
   `/path/to/release/scripts/repoctl upgrade plan --workspace-root /path/to/adopter --from /path/to/release --output /tmp/repoctl-upgrade-plan.json --json`
3. Inspect `operations`, `preserve_paths`, and `conflicts`.
4. Apply only the inspected plan:
   `/path/to/release/scripts/repoctl upgrade apply --workspace-root /path/to/adopter --plan-file /tmp/repoctl-upgrade-plan.json --json`
5. Inspect the `postflight` result emitted by apply. The upgraded runtime runs in a fresh process and reports repository identity, metadata, candidate, Reviewed Knowledge, and Graph state with typed recovery commands. Run it again directly when needed:
   `./scripts/repoctl upgrade postflight --json`
6. Run verification:
   `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/repoctl tests/maintenance`
   `./scripts/repoctl check --json`
   `./scripts/repoctl meta check --json`

The adopter test command covers managed runtime and workspace contracts. Template release/publication policy tests remain in the source repository CI and are not distributed to adopting workspaces.

## Manifest Policy

- `replace_paths` are managed control-plane files that may be replaced from the release.
- `create_paths` are canonical docs/examples that are copied only when missing.
- `preserve_paths` are adopter-owned state and must not be overwritten.
- `postflight_command`, when present, is the fixed `repoctl upgrade postflight --json` command. Arbitrary manifest commands are rejected.

Upgrade never rewrites task baselines, ownership decisions, completion receipts, archived tasks, or other preserved project state. These records remain byte-for-byte unchanged while managed control-plane code is replaced. The upgraded runtime accepts its current schemas and the immediately preceding task-state/completion-receipt pair through one isolated, fail-closed compatibility boundary because in-flight tasks and immutable receipts are persisted public data. That boundary does not rewrite records, infer missing transitions, add aliases, or create a separate legacy store; unsupported older state, incompatible candidates, unbound repositories, and invalid or stale Graph materialization remain explicit postflight findings.

A repo-scoped completion receipt keeps the `repo_id` that owned the task when it finished; a workspace-only receipt uses `repo_id: ""` and must not claim repository evidence. After a repository split or replacement, a namespace containing only repo-scoped completion receipts is historical evidence: postflight reports it under `historical_unbound_repo_ids`, but it does not require a currently configured repository identity. Graph materializations, pending candidates, reviewed Knowledge records, and Knowledge events remain current repository state and still fail closed when their `repo_id` is unbound. Remove or rebuild obsolete generated Graph state and review invalid pending candidates explicitly; never rewrite receipt provenance to make an old identity look current.

Each apply receipt records the managed source digest and backup tree digest. `./scripts/repoctl upgrade status --json` calculates backup `availability` as `available`, `missing`, `digest_mismatch`, or `not_required` without modifying the receipt. Backups use manual retention in this version; there is no prune command.

Workflow docs are distributed as `create_paths` by default. This lets new workspaces receive the canonical workflows while preserving modified workflows in existing workspaces.

## Forbidden Shortcuts

- Do not parse Backlog, PRD, task, or workflow prose to infer upgrade scope.
- Do not repair Board, task Markdown, archive, or metadata state inside upgrade apply.
- Do not use broad mirror sync or delete files absent from the release artifact.
- Do not update `repos/**` through this command.
