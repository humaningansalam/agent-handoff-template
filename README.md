# Agent Workspace Control Plane

A repo-aware workspace substrate for Claude Code, Codex, Cursor, and other coding agents.

It is not an autonomous agent runtime. It keeps current work, repository boundaries, restart instructions, and reusable project context stable while external agents do the reasoning and implementation.

## Core model

- **Task** holds the current job and its explicit Chosen file set.
- **Handoff** says exactly how to continue after a pause or transfer.
- **Context** finds source-linked material worth reading.
- **Knowledge** stores a reusable conclusion and why it matters.

The workspace root owns private control state and documentation. Product Git repositories live under `repos/`. Sparse file metadata lives inside each product repository under `.repometa/`. Graph and the persistent evidence index are derived navigation layers.

Repoctl deliberately does not create proof that tests ran. `## Verification` is an optional Task note. Context and Graph queries do not create selection receipts, Context Packs do not bind to Handoffs, and saving Knowledge does not start an approval lifecycle.

## Compared with adjacent tools

| Tool type | Focus | This project adds |
|---|---|---|
| Markdown task managers | Tasks and Kanban | Product-repository boundaries, start baselines, safe finish checks, and restartable Handoffs |
| Spec-driven tools | Spec -> plan -> tasks | Execution state after task intent already exists |
| Coding agents | Autonomous coding loop | A shared workspace contract that survives model and session changes |
| Code search and memory tools | Retrieval or long-term notes | Source-linked Context, typed Graph navigation, and optional reusable decisions in one repository-aware workspace |

## Use this when

- handoff quality matters more than chat history
- task files should be the unit of current work
- private workspace state and product code need separate Git boundaries
- several coding tools may work in the same workspace over time
- a large repository benefits from bounded Context and typed Graph navigation
- reusable decisions should remain linked to their sources

## Prerequisites

- Bash
- Git
- Python 3.11 or newer
- Node.js only for TypeScript/JavaScript semantic Graph analysis and the complete integration suite; other capabilities continue when that optional provider is unavailable

## Fresh adoption

For one product repository, clone it directly into `repos/`. Repoctl assigns that Git root the reserved ID `main`.

```bash
git clone <product-repo-url> repos
./scripts/repoctl repo list --json
./scripts/repoctl meta init --repo-id main --json
./scripts/repoctl meta check --repo-id main --json
./scripts/repoctl task create --start --json "First product change"
```

Review and commit `repos/.repometa/` inside the product repository. For a collection layout, place Git roots under `repos/<name>/`, run `./scripts/repoctl repo adopt --all --json`, and use the returned stable repo IDs.

Build Graph only when typed code navigation would help:

```bash
./scripts/repoctl graph build --repo-id main --json
```

`docs/PRD.md` is an adopter-owned seed. Replace it, delete it, or keep it as an index to private documents under `docs/prd/`.

## Daily work

For first entry, use [AGENTS.md](AGENTS.md#session-start-and-read-order) for policy and [docs/README.md](docs/README.md) for the document map. Confirm the product Git root with `repo list`, read its README and local guidance, and start requirements at `docs/PRD.md` (following relevant index links). Inspect implementation, manifests, tests and CI in that Git root before choosing changes or commands. Resume identifies current work; an empty Board does not establish implementation or release readiness.

At session start:

```bash
./scripts/repoctl task resume --json
```

`no_live` means there is no current task. `single_live` selects the only live task. `ambiguous` returns candidates; select one without writing state:

```bash
./scripts/repoctl task resume T-... --json
```

Only a current non-null `executable_handoff` is an execution instruction. `readable_handoff`, Board rows, and archived task history are inspection material.

For a normal product change:

```bash
./scripts/repoctl task create --start --json "Change title"
./scripts/repoctl task discovery add T-... --chosen repos/path/to/file --json
# edit and run the checks useful for the change
./scripts/repoctl task finish T-... --json
```

Use repeated `--chosen` arguments for one coherent multi-file scope. Add `--note` only for a scope decision or source reference worth retaining. Replace scope explicitly with `--replace-chosen ... --reason ...`.

Task finish checks repository identity, the start baseline, pre-existing dirty files, actual changes against Chosen, and changed-file `.repometa` requirements. It preserves optional `## Verification` text without interpreting it. If changes were committed after start, inspect with `task doctor --use-committed-diff` and finish with the same flag.

Before a real pause or transfer, write the four Handoff fields, remove the generated marker, and bind the reviewed restart state:

```bash
./scripts/repoctl task handoff bind T-... --json
```

A later task, Chosen, log, Verification note, child, Handoff, or repository change makes the binding inactive. Repoctl never executes the stored command or infers a replacement instruction.

## Finding code and context

Use direct reads, `rg`, or Graph when the identity is already known. For ambiguous work, ask Context for a bounded source-linked working set:

```bash
./scripts/repoctl context query "where is invoice retry decided" --repo-id main --json
./scripts/repoctl graph query --repo-id main --callers-of retry_invoice --in-file billing/retry.py --json
```

Context can combine current source, project authority, procedures, task history, Knowledge, and materialized Graph relations. It marks missing or stale derived data and never turns a ranked result into edit scope. Graph queries read the last materialized snapshot; refresh explicitly with `graph build` after relevant source changes.

A Task Context Pack is an optional one-time view or export:

```bash
./scripts/repoctl context pack --task T-... --repo-id main --format markdown
./scripts/repoctl context pack --task T-... --repo-id main --output /tmp/T-context.md --format markdown
```

The Pack is reference material. It does not become Task state, a Handoff binding, or a finish requirement.

## Reusing decisions

Most work needs only its archived Task. Save Knowledge when a decision, invariant, or failure mode is likely to matter across tasks:

```bash
./scripts/repoctl knowledge add \
  --repo-id main \
  --kind decision \
  --claim "Retries stop after the provider marks the request permanent." \
  --reason "Further retries duplicate charges and cannot recover." \
  --source docs/adr/retry-policy.md \
  --applies-to billing/retry.py \
  --json
```

Saving the record completes the action. `knowledge query` and Context can use it immediately. A changed source produces a warning while the saved reasoning remains visible. Save a new record with `--replaces K-...` when a decision changes. Upgrade preserves older Knowledge files and reports them for one-time agent migration; current queries use current records only.

When the reusable conclusion is ready during closeout, the corresponding `task finish --knowledge-kind --knowledge-claim --knowledge-reason` options save it atomically with task completion.

## Layout

```text
.
|-- AGENTS.md
|-- README.md
|-- docs/
|   |-- BOARD.md
|   |-- PRD.md
|   |-- tasks/
|   |-- archive/tasks/
|   |-- contracts/
|   |-- workflows/
|   `-- knowledge/records/
|-- repos/                  # ignored by root Git; product Git root(s)
|-- scripts/repoctl
|-- tools/repoctl/
`-- tests/repoctl/
```

Key contracts:

- [JSON output](docs/contracts/repoctl-json-contract.md)
- [Context and Task Packs](docs/contracts/repoctl-context-contract.md)
- [Graph](docs/contracts/repoctl-graph-contract.md)
- [Task Chosen scope](docs/contracts/repoctl-task-scope-contract.md)
- [Module boundaries](docs/contracts/repoctl-module-boundaries.md)
- [Debug mode](docs/contracts/repoctl-debug-contract.md)

## Durable boundaries

- Task frontmatter is status authority; `docs/BOARD.md` is the live registry.
- Product mutation starts from a recorded repository baseline. Existing dirty files stay outside task ownership unless explicitly resolved.
- The Task's Chosen set is the only current edit-scope record. Context, Graph, history, Backlog, and retired outcome state do not create scope.
- Standalone completed tasks archive under `docs/archive/tasks/`. Completion receipts preserve lifecycle and repository-change history; their content hashes identify the archived record and make no test claim.
- `.repometa` provides sparse human metadata and changed-file gates. Graph and the code index remain derived.
- Context, Graph, and Knowledge preserve source identity and repository namespace. They provide evidence and navigation rather than authority.
- Upgrade postflight reports stale derived Graph or Knowledge projections as maintenance with exit status 0 when durable state is otherwise valid. An unused missing Graph and empty Knowledge need no initialization.

## Debug mode

Set `"debug_mode": true` in `docs/repoctl.json` to append sanitized, bounded command diagnostics to the ignored file `docs/tasks/.repoctl-state/debug/events.jsonl`. Debug capture does not change arguments, output, exit status, Task state, Handoff freshness, product files, or authority. Inspect it with:

```bash
./scripts/repoctl debug summary --json
```

The journal helps locate costly or failing command shapes. It does not prove that an output was used or that work was correct.
