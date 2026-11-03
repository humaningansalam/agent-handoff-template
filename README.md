# Agent Workspace Control Plane

A repo-aware shared workspace for coding agents, models, and sessions.

It is not an autonomous agent runtime. It keeps current work, repository boundaries, restart instructions, and reusable project context stable while external agents do the reasoning and implementation.

## Core model

- **Task** holds the current goal, narrow Chosen scope, decisions, blockers, next action, and references.
- **Board** lists live Tasks and deferred work; **Workflows** capture repeatable or risky procedures.
- **Graph** finds implementation structure, callers, dependents, and tests, with explicit precision and freshness.
- **Knowledge** stores a reusable claim, its reason, and its sources.
- **Handoff** is the four-field current restart instruction inside a Task, not a history store.

Source retrieval and ranking are internal Graph/Knowledge read helpers, not additional durable product planes or mandatory user actions.

The workspace root owns private control state and documentation. Product Git repositories live under `repos/`. Sparse file metadata lives inside each product repository under `.repometa/`. Graph and the persistent evidence index are derived navigation layers.

Repoctl deliberately does not create proof that tests ran. `## Verification` is an optional Task note. Graph search and Knowledge lookup do not create selection receipts, and saving Knowledge does not start an approval lifecycle.

## Compared with adjacent tools

| Tool type | Focus | This project adds |
|---|---|---|
| Markdown task managers | Tasks and Kanban | Product-repository boundaries, start baselines, safe finish checks, and restartable Handoffs |
| Spec-driven tools | Spec -> plan -> tasks | Execution state after task intent already exists |
| Coding agents | Autonomous coding loop | A shared workspace contract that survives model and session changes |
| Code search and memory tools | Retrieval or long-term notes | Source-linked Graph search, typed Graph navigation, and optional reusable decisions in one repository-aware workspace |

## Use this when

- handoff quality matters more than chat history
- task files should be the unit of current work
- private workspace state and product code need separate Git boundaries
- several coding tools may work in the same workspace over time
- a large repository benefits from bounded source lookup and typed Graph navigation
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

`docs/PRD.md` is the adopter-owned fixed entrypoint. Rewrite its content for the product and link to the needed `docs/architecture/`, `docs/specs/`, `docs/adr/`, and `docs/research/` documents. Do not create empty folders or boilerplate-only documents. Older `docs/prd/**` is not part of the current read contract; migrate its useful contents to the documented structure before adoption.

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

Task finish checks repository identity, the start baseline, pre-existing dirty files, actual changes against Chosen, and changed-file `.repometa` requirements. It preserves optional `## Verification` text without interpreting it. If task changes were committed after start, inspect with `task doctor --use-committed-diff` and finish with the same flag.

Before a real pause or transfer, write the four Handoff fields, remove the generated marker, and bind the reviewed restart state:

```bash
./scripts/repoctl task handoff bind T-... --json
```

Meaningful changes to Goal/Task contract, Chosen/Notes, child lifecycle, Handoff, or repository observation invalidate binding. Routine Execution Log additions and optional Verification prose do not. When a failed check changes the next step or a constraint, update the current Task/Handoff explicitly. Repoctl never executes the stored command.

## Finding code and sources

Use direct reads, `rg`, or Graph when the identity is already known. For ambiguous work, use Graph source search for a bounded cited working set:

```bash
./scripts/repoctl graph search "where is invoice retry decided" --repo-id main --json
./scripts/repoctl graph query --repo-id main --callers-of retry_invoice --in-file billing/retry.py --json
./scripts/repoctl graph workflow --repo-id main --route /predict --json
./scripts/repoctl graph workflow --repo-id main --symbol audio_clock --json
```

Graph source search combines current source, documents, history, Knowledge, and Graph. It does not create edit scope. `graph query` reads typed materialized relations; `graph workflow` adds bounded live-source hints for route and source navigation, explicitly labeling non-confirmed links as candidates. Refresh explicitly with `graph build` after source changes; a stale snapshot never proves zero impact.

No export registration step is part of the Task lifecycle. Keep current reasoning in Task and reusable conclusions in Knowledge; open named source files directly.

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

Saving the record completes the action. `knowledge query` and Graph evidence searches can read it immediately. A changed source produces a warning while the saved reasoning remains visible. Save a new record with `--replaces K-...` when a decision changes. Older v1 Knowledge is one-time conversion input, not a supported query format: run `knowledge migrate --record-id K-... --apply`, review its current v2 successor, then `knowledge migrate --record-id K-... --finalize --apply`. Finalization archives byte-identical v1 evidence and updates exact citations; unresolved provenance blocks conversion. A missing or stale projection falls back to bounded, validated v2 originals and emits a warning.

When the reusable conclusion is ready during closeout, the corresponding `task finish --knowledge-kind --knowledge-claim --knowledge-reason` options save it atomically with task completion.

## Layout

```text
.
|-- AGENTS.md
|-- README.md
|-- docs/
|   |-- BOARD.md
|   |-- PRD.md             # fixed entry + read map
|   |-- architecture/       # when an overall design is needed
|   |-- specs/              # requirements and design by capability
|   |-- adr/                # consequential decisions
|   |-- research/           # evidence, not product authority
|   |-- tasks/              # current execution, not source specs
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
- [Graph source evidence](docs/contracts/repoctl-evidence-contract.md)
- [Graph](docs/contracts/repoctl-graph-contract.md)
- [Task Chosen scope](docs/contracts/repoctl-task-scope-contract.md)
- [Module boundaries](docs/contracts/repoctl-module-boundaries.md)
- [Debug mode](docs/contracts/repoctl-debug-contract.md)

## Durable boundaries

- Task frontmatter is status authority; `docs/BOARD.md` is the live registry.
- Product mutation starts from a recorded repository baseline. Existing dirty files stay outside task ownership unless explicitly resolved.
- The Task's Chosen set is the only current edit-scope record. Source search, Graph, history, Backlog, and retired outcome state do not create scope.
- Standalone completed tasks archive under `docs/archive/tasks/`. Completion receipts preserve lifecycle and repository-change history; their content hashes identify the archived record and make no test claim.
- `.repometa` provides sparse human metadata and changed-file gates. Graph and the code index remain derived.
- Graph and Knowledge preserve source identity and repository namespace. They provide evidence and navigation rather than authority.
- Upgrade postflight reports stale derived Graph or Knowledge projections as maintenance with exit status 0 when durable state is otherwise valid. An unused missing Graph and empty Knowledge need no initialization.

## Debug mode

Set `"debug_mode": true` in `docs/repoctl.json` to append sanitized, bounded command diagnostics to the ignored file `docs/tasks/.repoctl-state/debug/events.jsonl`. Debug capture does not change arguments, output, exit status, Task state, Handoff freshness, product files, or authority. Inspect it with:

```bash
./scripts/repoctl debug summary --json
```

The journal helps locate costly or failing command shapes. It does not prove that an output was used or that work was correct.
