# Project Task System

This directory contains the live task registry, task files, workflows, and archive.

## Core files

- `BOARD.md` — live task registry
- `tasks/` — active/reopenable task files plus canonical `repoctl task create` templates
- `workflows/` — reusable procedures
- `archive/` — non-live task originals
- `PRD.md` — optional stable project context

## Rules

- Task frontmatter is authoritative for status.
- `BOARD.md` records only which tasks are live in the `## Board` section.
- `## Backlog` is for planned raw items without a task file.
- Task filenames use canonical UTC IDs + English lowercase kebab-case slugs.
- Standalone tasks move to `archive/` when done or canceled.
- `tasks/TEMPLATE.md` and `tasks/PARENT_TEMPLATE.md` are creation inputs, not example tasks.

## Common commands

- Create task: `./scripts/repoctl task create "Task title"`
- Create with explicit slug: `./scripts/repoctl task create --slug my-slug "Task title"`
- Create parent task: `./scripts/repoctl task create --type parent "Parent title"`
- List backlog items: `./scripts/repoctl backlog list`
- Add backlog item: `./scripts/repoctl backlog add "Short backlog title" --body-file /tmp/backlog.md`
- Show backlog item: `./scripts/repoctl backlog show BL-...`
- Remove backlog item: `./scripts/repoctl backlog remove BL-...`
- Promote backlog item: read the item, inspect repo context, then run `./scripts/repoctl task create --backlog-id BL-... --slug my-slug --area repo --repo-id <id> "Task title"` for configured multi-repo product work, record `## Discovery` before repo changes are finished, and refine the task file.
- Show live tasks: `./scripts/repoctl task list --json`
- Show a task: `./scripts/repoctl task show T-... --json`
- Diagnose finish readiness: `./scripts/repoctl task doctor T-... --json`
- Record Discovery evidence: `./scripts/repoctl task discovery add T-... --query "..." --reviewed repos/path --chosen repos/path --json`
- Append execution log: `./scripts/repoctl task log append T-... "message" --json`
- Finish task: `./scripts/repoctl task finish T-... --verification-file /tmp/T-...-verification.md --json`
- Scan task statuses: `rg "^status:" docs/tasks/T-*.md`
- Initialize repo metadata store: `./scripts/repoctl meta init`
- Find annotated/default metadata matches: `./scripts/repoctl meta query --topic auth --json`
- Suggest candidate files from explicit text: `./scripts/repoctl meta suggest "login flow" --json`
- Extract read-only code facts: `./scripts/repoctl index code --json`
- Build a read-only Graph snapshot: `./scripts/repoctl graph build --repo-id main --json`
- Query the derived Graph snapshot: `./scripts/repoctl graph query --repo-id main --file src/app.py --json`
- Query evidence context: `./scripts/repoctl context query "Why is Graph non-authoritative?" --repo-id main --json`
- Query evidence context with knowledge source-status explanation: `./scripts/repoctl context query "Why is Graph non-authoritative?" --repo-id main --explain --json`
- Run release-candidate field gates and write a summary artifact: `./scripts/repoctl field-gate run release-candidate --repo-id main --output .repoctl-state/field-gates/release-candidate.json --json`
- Remove only field-gate-created fixture files whose recorded digest still matches: `./scripts/repoctl field-gate cleanup --artifact .repoctl-state/field-gates/release-candidate.json --json`
- Compare release-candidate field gate artifacts: `./scripts/repoctl field-gate compare --baseline .repoctl-state/field-gates/baseline.json --candidate .repoctl-state/field-gates/candidate.json --max-failed-count-increase 0 --require-same-gates --require-no-gate-regressions --json`
- Materialize a controlled benchmark corpus: `./scripts/repoctl context benchmark-materialize --fixture tests/fixtures/context-benchmark --repo-id main --json`
- Benchmark context and reviewed-knowledge retrieval: `./scripts/repoctl context benchmark --repo-id main --json`
- Enforce benchmark gates: `./scripts/repoctl context benchmark --repo-id main --min-recall-at-5 0.8 --min-knowledge-recall-at-5 1.0 --require-source-integrity --json`
- Pack task startup context: `./scripts/repoctl context pack --task T-... --repo-id main --json`
- Pack task startup context with source-status explanation: `./scripts/repoctl context pack --task T-... --repo-id main --explain --json`
- Compare task context pack artifacts: `./scripts/repoctl context pack-compare --baseline .repoctl-state/context-pack/baseline.json --candidate .repoctl-state/context-pack/candidate.json --json`
- Materialize task context pack benchmark tasks: `./scripts/repoctl context pack-benchmark-materialize --fixture tests/fixtures/context-pack-benchmark --json`
- Benchmark task startup context packs: `./scripts/repoctl context pack-benchmark --fixture tests/fixtures/context-pack-benchmark --repo-id main --output .repoctl-state/context-pack-benchmark/result.json --json`
- Compare context pack benchmark artifacts: `./scripts/repoctl context pack-benchmark-compare --baseline .repoctl-state/context-pack-benchmark/baseline.json --candidate .repoctl-state/context-pack-benchmark/candidate.json --max-mean-must-read-recall-drop 0 --json`
- Build a review-only knowledge candidate: `./scripts/repoctl knowledge candidate build --source docs/adr/example.md --repo-id main --kind decision --json`
- Build a candidate from completed task evidence: `./scripts/repoctl knowledge candidate build --from-receipt T-... --repo-id main --kind invariant --json`
- Build a candidate from a current context pack: `./scripts/repoctl knowledge candidate build --from-pack .repoctl-state/context-pack/T-....json --repo-id main --kind decision --json`
- Check candidate quality before review: `./scripts/repoctl knowledge candidate check KC-... --repo-id main --json`
- Check all pending candidates: `./scripts/repoctl knowledge candidate check --all --repo-id main --json`
- Check candidates across all lifecycle states: `./scripts/repoctl knowledge candidate check --all --all-states --repo-id main --json`
- List candidates with check summaries: `./scripts/repoctl knowledge candidate list --repo-id main --with-checks --json`
- Review a candidate as Markdown before approval: `./scripts/repoctl knowledge candidate show KC-... --repo-id main --format markdown`
- Refresh a stale candidate without mutating it: `./scripts/repoctl knowledge candidate refresh KC-... --repo-id main --json`
- Refresh all stale candidates non-destructively: `./scripts/repoctl knowledge candidate refresh --all-stale --repo-id main --json`
- Refresh stale reviewed records into review-only candidates: `./scripts/repoctl knowledge candidate refresh --all-stale --include-records --repo-id main --json`
- Refresh one stale reviewed record into a review-only candidate: `./scripts/repoctl knowledge candidate refresh --record-id K-... --repo-id main --json`
- Show knowledge workflow status and check summaries: `./scripts/repoctl knowledge status --repo-id main --json`
- List append-only knowledge events: `./scripts/repoctl knowledge event list --repo-id main --json`
- Show one knowledge event: `./scripts/repoctl knowledge event show E-... --repo-id main --json`
- Approve a candidate into reviewed knowledge: `./scripts/repoctl knowledge approve KC-... --repo-id main --reviewed-by <label> --note-file /tmp/review-note.md --json`
- Approve a replacement decision: `./scripts/repoctl knowledge approve KC-... --repo-id main --supersedes K-... --reviewed-by <label> --note-file /tmp/review-note.md --json`
- Reject a candidate with review evidence: `./scripts/repoctl knowledge reject KC-... --repo-id main --reason-file /tmp/reason.md --json`
- Deprecate a record with append-only lifecycle evidence: `./scripts/repoctl knowledge deprecate K-... --repo-id main --reason-file /tmp/reason.md --json`
- Show a reviewed knowledge record: `./scripts/repoctl knowledge show K-... --repo-id main --json`
- Query reviewed knowledge: `./scripts/repoctl knowledge query "current auth decision" --repo-id main --json`
- Query with source-status explanation: `./scripts/repoctl knowledge query "current auth decision" --repo-id main --explain --json`
- Query historical deprecated knowledge explicitly: `./scripts/repoctl knowledge query "old auth decision" --repo-id main --include-deprecated --json`
- Query all historical statuses explicitly: `./scripts/repoctl knowledge query "old auth decision" --repo-id main --include-history --json`
- Check knowledge source drift: `./scripts/repoctl knowledge check --repo-id main --json`
- Check records and candidates together: `./scripts/repoctl knowledge check --repo-id main --include-candidates --json`
- Render non-authoritative static wiki pages: `./scripts/repoctl knowledge render --repo-id main --json`
- Check rendered knowledge pages without rewriting them: `./scripts/repoctl knowledge render --repo-id main --check --json`
- Check changed-file metadata gate: `./scripts/repoctl meta check --changed --json`

## Notes

- Read `AGENTS.md` for the full operating contract.
- Read `docs/contracts/repoctl-json-contract.md` before wrapping repoctl with MCP or other machine clients.
- Read `docs/contracts/repoctl-graph-contract.md` before consuming `repoctl graph build` output.
- Read `docs/contracts/repoctl-module-boundaries.md` before changing repoctl internals.
- Read `docs/workflows/v0-foundation-field-test.md` before starting MCP, Graph, or llmwiki work.
- Command examples use the workspace wrapper. If `repoctl` is installed on `PATH`, the shorter `repoctl ...` form is equivalent.
- `scripts/repoctl` resolves the workspace root from the script location, so invoking it by explicit path from `repos/` or nested directories is also supported.
- The workspace root may not be a usable Git worktree. Run product Git commands in `repos/` or `repos/<repo-id>/`, not from the workspace root.
- Backlog text is free-form human planning text. repoctl manages backlog items as opaque raw blocks with content-hash IDs, but it does not infer files, scope, validation, metadata, or task body sections from that text.
- For PRD or external-note triage, use `docs/workflows/prd-backlog-sequential.md` to list gaps as Backlog items and promote them one at a time.
- `repoctl meta suggest` is a discovery aid only. The agent must inspect candidate files and record structured `## Discovery` with `repoctl task discovery add`; suggestions are not authoritative scope.
- `repoctl index code` extracts technical facts such as language, imports, symbols, calls, deps, and observed effect hints without writing `.repometa` or creating Graph state.
- `repoctl graph build` derives a deterministic snapshot from repo registry, code index, and `.repometa`; it does not mutate source authorities or resolve symbols/imports.
- `repoctl context` returns temporary source bundles and separate reviewed-knowledge matches; `context pack` exposes reviewed knowledge in its own group and does not create durable facts or change task scope.
- `repoctl field-gate run release-candidate` is an explicit mutating runner for release-candidate field tests; it records per-gate summaries and digests without parsing human output, checks knowledge source drift, and includes multi-repo isolation gates when `web` and `api` repositories are configured.
- `repoctl field-gate cleanup` removes only artifact-recorded `created_file` entries whose current digest matches the recorded digest, then prunes empty parents only up to the recorded boundary.
- `repoctl field-gate compare` validates field-gate artifact digests before comparing gate sets, failed counts, per-gate status, and numeric summary deltas; valid failed run artifacts remain comparable for regression triage.
- `repoctl context benchmark-materialize` is the explicit mutating setup step for controlled benchmark fixtures; `context benchmark` itself remains read-only.
- `repoctl context pack-benchmark-materialize` is the explicit mutating setup step for archived fixture tasks; `context pack-benchmark` itself remains read-only.
- `repoctl context pack-benchmark` and `pack-benchmark-compare` are retrieval/packing gates for field tests; they measure required source recall and do not validate task scope or generated answers.
- `repoctl knowledge candidate` writes review inputs under `.repoctl-state/`, which is ignored by Git.
- `repoctl knowledge candidate build --from-pack` uses the pack only to select current authority source refs; the pack artifact itself does not become a canonical knowledge source.
- `repoctl knowledge candidate refresh` creates a new candidate plus an event; it does not edit the stale candidate in place.
- `repoctl knowledge candidate refresh --include-records` turns stale reviewed records into non-authoritative candidates with original-record provenance; approval supersedes the original record instead of editing it.
- `repoctl knowledge candidate list` and `knowledge status` derive candidate review state from append-only events.
- `repoctl knowledge approve` creates reviewed records under `docs/knowledge/records/` and append-only events under `docs/knowledge/events/`; reviewer labels, review notes, source digest sets, timestamps, and supersession links are stored as provenance; `knowledge query` excludes stale and superseded records by default.
- `repoctl knowledge render` writes ignored non-authoritative static wiki pages under `docs/knowledge/generated/` for `main` and `docs/knowledge/generated/<repo-id>/` for other repo IDs. It includes `INDEX.md`, kind pages, per-record pages, file target pages, lifecycle history, `search-index.json`, and a manifest. `knowledge render --check` verifies those pages, internal links, stale owned pages, and manifest freshness without rewriting them.
- Files under `examples/` are reference examples only; repoctl does not use them as creation templates.
