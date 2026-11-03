# Knowledge

This directory stores optional reusable decisions, invariants, and failure modes for repoctl.

Most implementation detail belongs in Task history or an existing ADR, contract, workflow, or product document. Add a Knowledge record only when a short conclusion and its reason are likely to help across tasks.

```bash
./scripts/repoctl knowledge add \
  --repo-id main \
  --kind decision \
  --claim "the reusable conclusion" \
  --reason "why it applies and what problem it avoids" \
  --source docs/adr/example.md \
  --json
```

Use `--workspace` instead of `--repo-id` for root-only Task or tooling sources, including in workspaces with product repositories. Workspace records use `repo_id: ""`; they cannot cite `repos/` sources or declare product applicability. Root-only `task finish --knowledge-*` selects this scope automatically. `knowledge query --workspace "the conclusion"` and `graph query --workspace --task T-... --json` retrieve the saved conclusion and its completed Task provenance.

`records/` is the durable store. A current record is complete as soon as it is saved; `applies_to`, `replaces`, and `author` are optional. Source paths provide provenance. If a source later changes, queries keep the recorded conclusion visible and report the drift as a warning.

Use `--replaces K-...` when a new record explicitly corrects an old one. Use `knowledge query --include-history` when replaced current-format records matter.

Older v1 records are one-time conversion input, not normal query state. `knowledge migrate --repo-id main` inventories them; stage one record with `--record-id K-... --apply` only after verifying its real reason and source, then review `--finalize` and explicitly `--finalize --apply`. Finalization moves byte-identical v1 evidence to `docs/archive/knowledge/`, updates only exact source-digest citations, and leaves no active v1 duplicate. `knowledge check` reports each unfinalized v1; unresolved historical meaning requires manual review, not invented reasons. Projection damage does not hide healthy direct v2 originals. The derived projection under `.repoctl-state/knowledge/<repo-id>/current-head.json` uses `_workspace` for workspace scope and is rebuildable with `knowledge rebuild`; it is an index, not a second source of truth.

Knowledge never defines Task scope, source authority, or an execution instruction. Follow its source links and inspect current code before acting.
