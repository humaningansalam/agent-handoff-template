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

`records/` is the durable store. A current record is complete as soon as it is saved; `applies_to`, `replaces`, and `author` are optional. Source paths provide provenance. If a source later changes, queries keep the recorded conclusion visible and report the drift as a warning.

Use `--replaces K-...` when a new record explicitly corrects an old one. Use `knowledge query --include-history` when replaced current-format records matter.

Upgrade leaves older records and `events/` files untouched. They are migration input, not query state: an agent reads the useful conclusion and saves a new current record before relying on it. `knowledge check` and upgrade postflight report records that still need this step. The derived projection under `.repoctl-state/knowledge/<repo-id>/current-head.json` is rebuildable with `knowledge rebuild`; it is an index, not a second source of truth.

Knowledge never defines Task scope, source authority, or an execution instruction. Follow its source links and inspect current code before acting.
