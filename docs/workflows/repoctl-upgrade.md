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
- existing `AGENTS.md` operating rules (seeded only when missing)

## Flow

1. Obtain a repoctl release checkout or extracted release artifact.
2. Run the release artifact's updater against the adopter workspace. This avoids executing an older adopter runtime across the self-update boundary:
   `/path/to/release/scripts/repoctl upgrade plan --workspace-root /path/to/adopter --from /path/to/release --output /tmp/repoctl-upgrade-plan.json --json`
3. Inspect `operations`, `preserve_paths`, and `conflicts`.
4. Apply only the inspected plan:
   `/path/to/release/scripts/repoctl upgrade apply --workspace-root /path/to/adopter --plan-file /tmp/repoctl-upgrade-plan.json --json`
5. Inspect the immediate `postflight` diagnostic emitted by apply. The upgraded runtime runs in a fresh process and reports repository identity, metadata, completion history, Knowledge projection, and Graph state. Old preserved authority may be reported as unresolved here; this is **not** a passed final transition. Complete the separately reviewed one-time conversions before the final postflight. Rebuildable derived state is optional maintenance, not substitute source evidence. Rerun postflight directly:
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

Each managed write rechecks its source and target content against the reviewed plan. On apply failure, rollback reverses only bytes verified as its own writes; a concurrent owner edit is preserved and reported as `upgrade_rollback_incomplete` with exact `rollback_conflicts` in the run's `rollback.json`. Original backup files remain for deliberate reconciliation; do not blindly rerun apply or restore over the external edit.

Upgrade run directories are created exclusively. If a run ID already exists (including a same-second retry), `upgrade_run_collision` stops before changing managed files; wait and generate a new plan/run rather than reusing or deleting the existing backup/receipt directory.

The upgraded runtime accepts only current Task, Handoff, Discovery and Knowledge v2 records, v5 completion receipts, and v4 Task start baselines. Upgrade preserves old authority bytes but **does not parse old schemas as normal input or migrate user data automatically**. Follow [adopter-transition](adopter-transition.md) after a verified source release and owner approval: preview and convert old receipts with byte-identical history witnesses, explicitly retire only closed old start states, stage/finalize genuine v1 Knowledge, and relink exact cited receipt sources one Task at a time after v1 finalization. Old v2 receipts that never recorded start or transition evidence retain an explicit unverified historical marker; do not invent it or rewrite the completed Task hash.

Inspect and repair live Task/Handoff meaning only from real current evidence. Unbound repositories, source digest drift and invalid authority remain reported problems, never silent legacy compatibility. A stale Graph or Knowledge projection can be rebuilt after source conversion; a missing projection is not proof of lost original evidence. Rebuild derived history, Graph and Knowledge as appropriate and rerun final postflight.

A repo-scoped completion receipt keeps the `repo_id` that owned the Task when it finished; a workspace-only receipt uses `repo_id: ""` and must not claim repository evidence. After a repository split or replacement, a namespace containing only repo-scoped completion receipts is historical evidence: postflight reports it under `historical_unbound_repo_ids`, but it does not require a currently configured repository identity. Product Graph and Knowledge state still requires a configured repository identity; explicit workspace evidence uses `repo_id: ""` and the reserved `_workspace` derived namespace. Remove or rebuild obsolete derived state; never rewrite receipt provenance to make an old identity look current.

Each apply receipt records the managed source digest and backup tree digest. `./scripts/repoctl upgrade status --json` calculates backup `availability` as `available`, `missing`, `digest_mismatch`, or `not_required` without modifying the receipt. Pre-digest receipts remain readable as `digest_unavailable`. Backups use manual retention in this version; there is no prune command.

Workflow docs are distributed as `create_paths` by default. This lets new workspaces receive the canonical workflows while preserving modified workflows in existing workspaces.

`AGENTS.md` also uses `create_paths`: the installed workspace's operating policy is adopter-owned. Upgrade neither replaces local rules nor merges upstream policy changes. Compare relevant upstream rule changes when updating the runtime; postflight checks machine state, not the meaning or compatibility of policy prose. No policy receipt or extra approval step is required.

## Forbidden Shortcuts

- Do not parse Backlog, PRD, task, or workflow prose to infer upgrade scope.
- Do not repair Board, task Markdown, archive, or metadata state inside upgrade apply.
- Do not use broad mirror sync or delete files absent from the release artifact.
- Do not update `repos/**` through this command.
