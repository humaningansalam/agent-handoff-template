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
- Benchmark context retrieval: `./scripts/repoctl context benchmark --repo-id main --json`
- Pack task startup context: `./scripts/repoctl context pack --task T-... --repo-id main --json`
- Build a review-only knowledge candidate: `./scripts/repoctl knowledge candidate build --source docs/adr/example.md --repo-id main --kind decision --json`
- Approve a candidate into reviewed knowledge: `./scripts/repoctl knowledge approve KC-... --repo-id main --json`
- Query reviewed knowledge: `./scripts/repoctl knowledge query "current auth decision" --repo-id main --json`
- Check knowledge source drift: `./scripts/repoctl knowledge check --repo-id main --json`
- Render non-authoritative knowledge pages: `./scripts/repoctl knowledge render --repo-id main --json`
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
- `repoctl context` returns temporary source bundles and separate reviewed-knowledge matches; it does not create durable facts or change task scope.
- `repoctl knowledge candidate` writes review inputs under `.repoctl-state/`, which is ignored by Git.
- `repoctl knowledge approve` creates reviewed records under `docs/knowledge/records/` and append-only events under `docs/knowledge/events/`; `knowledge query` excludes stale records by default.
- Files under `examples/` are reference examples only; repoctl does not use them as creation templates.
