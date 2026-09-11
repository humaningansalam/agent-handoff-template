# repoctl Upgrade Workflow

Use this workflow to update the workspace control plane in an adopting workspace without overwriting project state.

## Scope

`repoctl upgrade` updates manifest-managed control-plane files such as `scripts/repoctl`, `tools/repoctl/**`, task templates, and contracts. It adds canonical workflow docs only when they are missing.

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
5. Inspect the `postflight` result emitted by apply. The upgraded runtime runs in a fresh process and reports repository identity, metadata, completion history, Knowledge projection, and Graph state. Rebuildable derived state appears as optional maintenance and does not make a successful install fail. Run postflight again directly when needed:
   `./scripts/repoctl upgrade postflight --json`
6. Run workspace health checks:
   `./scripts/repoctl check --json`
   `./scripts/repoctl meta check --json`

An unused, absent Graph or empty Knowledge projection needs no initialization action. Postflight suggests maintenance only for derived state that exists or is required by durable records.

The release archive contains runtime field-gate fixtures, not Python test modules. Source tests and release/publication policy checks remain in the source repository CI and are not distributed to adopting workspaces.

## Manifest Policy

- `replace_paths` are managed control-plane files that may be replaced from the release.
- `create_paths` are canonical docs/examples that are copied only when missing.
- `preserve_paths` are adopter-owned state and must not be overwritten.
- `postflight_command`, when present, is the fixed `repoctl upgrade postflight --json` command. Arbitrary manifest commands are rejected.

Planning is read-only and rejects managed-content drift when the adopter already reports the source release version. Existing `create_paths` are adopter-owned and excluded from that identity because upgrade never overwrites them; missing `create_paths` remain planned creates. There is no same-version override; publish and use a new version for changed managed content.

Upgrade never rewrites task baselines, ownership decisions, completion receipts, archived tasks, or other preserved authority state. These records remain byte-for-byte unchanged while managed control-plane code is replaced. One explicit versioned migration may create a derived fixed archive locator when an exact live `follow_up_of` identity has no valid completion-receipt lookup and resolves to exactly one regular, non-symlink archived task with matching canonical ID and terminal status. The migration, authority fingerprints, target content, and existing-target state are visible in and bound to the inspected plan; any missing, ambiguous, changed, invalid, conflicting, or escaping input fails closed before apply mutates the workspace. Apply receipts record planned and applied migrations, and rollback removes a newly published locator. No archived task or receipt is changed or inferred.

The upgraded runtime executes current Task, Handoff, Discovery, and Knowledge formats only. Upgrade preserves older Task prose, logs, Knowledge files, and machine state without rewriting them. An agent reviews live Task/Handoff state once, writes current Chosen/Notes or binds the current Handoff as needed, and recreates useful older Knowledge as current records. Historical completion receipts retain the minimum read boundary needed by finished work and open parents. Unbound repositories and invalid authority state remain errors. A stale Graph or Knowledge projection is reported as `ready_with_maintenance` with an explicit rebuild action and exit 0.

A repo-scoped completion receipt keeps the `repo_id` that owned the Task when it finished; a workspace-only receipt uses `repo_id: ""` and must not claim repository evidence. After a repository split or replacement, a namespace containing only repo-scoped completion receipts is historical evidence: postflight reports it under `historical_unbound_repo_ids`, but it does not require a currently configured repository identity. Current Graph and Knowledge state still requires a configured repository identity. Remove or rebuild obsolete derived state; never rewrite receipt provenance to make an old identity look current.

Each apply receipt records the managed source digest and backup tree digest. `./scripts/repoctl upgrade status --json` calculates backup `availability` as `available`, `missing`, `digest_mismatch`, or `not_required` without modifying the receipt. Pre-digest receipts remain readable as `digest_unavailable`. Backups use manual retention in this version; there is no prune command.

Workflow docs are distributed as `create_paths` by default. This lets new workspaces receive the canonical workflows while preserving modified workflows in existing workspaces.

## Forbidden Shortcuts

- Do not parse Backlog, PRD, task, or workflow prose to infer upgrade scope.
- Do not repair Board, task Markdown, archive, or metadata state inside upgrade apply.
- Do not use broad mirror sync or delete files absent from the release artifact.
- Do not update `repos/**` through this command.
