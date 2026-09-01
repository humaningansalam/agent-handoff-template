# repoctl Usability Director Step — 2026-09-02

This is the complete implementation packet issued by the active Main Director for the current checkout. `docs/PRD.md` is the Foundation, but agreement among documentation, contracts, implementation, and tests is not accepted as proof of usability. A path passes only when a real operator can complete its success, failure, and recovery journey through the public CLI, with less code, persisted state, ceremony, and conceptual duplication than the current implementation.

This step issues implementation work only and makes no completion declaration.

## Director boundary and current checkout

- Workspace: `/mnt/data/workspace/git/agent-handoff-template`
- Fixed DevSpace workspace ID: `ws_ab600f24e5`
- Foundation: `docs/PRD.md`
- Scope: root control plane only; no product task was created.
- HEAD: `ad93d50764dbb9d222a5c52ff2e72441c30f0215`
- `origin/main`: `ed3987eac98db017509ce695c49361069b2091ec`
- Branch distance: `main` is 19 commits ahead of `origin/main`.
- `v0.9.0`: `ed3987eac98db017509ce695c49361069b2091ec`
- Index: clean.
- Continuation-entry worktree: tracked usability repairs were already present across ten files; this continuation retained them, added the existing-tag verification repair, and replaced this review with the corrected packet below.

Current tracked repair files:

- `.github/workflows/release.yml`
- `tools/repoctl/cli.py`
- `tests/repoctl/graph/test_graph_query.py`
- `tests/repoctl/repository/test_repository_adoption.py`
- `tests/repoctl/task/test_task_finish.py`
- `tests/repoctl/task/test_task_lifecycle.py`
- `tests/repoctl/test_cli_discoverability.py`
- `tests/repoctl/workspace/test_check.py`
- `tests/repoctl/workspace/test_check_validation.py`
- `tests/test_release_repository.py`

This review is intentionally untracked until the implementation handoff is accepted.

[Source: `docs/PRD.md`; `AGENTS.md`; `git rev-parse HEAD`; `git rev-parse origin/main`; `git rev-list --count origin/main..HEAD`; `git status --short --branch`; `git diff`; `git diff --cached`; `git rev-parse v0.9.0^{}`]

## Exhaustive audit inventory

### Public parser and help surface

`build_parser()` was recursively traversed. Every parser node was then invoked through the real wrapper as `./scripts/repoctl <path> --help`.

- 89 parser nodes
- 19 command groups, including the root
- 70 leaf commands
- 89/89 help invocations exited zero
- 89/89 parsers have no description
- 35 positional arguments exist; 35/35 have no help text
- 39 required options exist; 37/39 have no help text
- 251 option actions have no help text
- `version`, `version --json`, and `--version` are raw-argv special cases outside argparse
- `version --help` is an invalid command
- `context query` and `context pack` expose both `--json` and `--format json`

Current non-archive operator and contract documentation mentions 44 of the 70 leaf commands. The following 26 visible leaves have no literal operator-documentation entry:

`field-gate run`, `field-gate compare`, `repo show`, `repo check`, `task doctor`, `task baseline resolve`, `task block`, `task cancel`, `backlog remove`, `context benchmark`, `context benchmark-materialize`, `context benchmark-compare`, `context pack-compare`, `context pack-benchmark`, `context pack-benchmark-materialize`, `context pack-benchmark-compare`, `knowledge candidate list`, `knowledge candidate show`, `knowledge candidate check`, `knowledge candidate refresh`, `knowledge status`, `knowledge event list`, `knowledge event show`, `knowledge reject`, `knowledge deprecate`, and `knowledge check`.

[Source: `tools/repoctl/cli.py`; `README.md`; `AGENTS.md`; `docs/README.md`; `docs/tasks/README.md`; `docs/workflows/*.md`; `docs/contracts/*.md`; recursive argparse/help and documentation census executed in this Director step]

### JSON envelope and continuation surface

Every leaf was called in a fresh extracted release workspace as `./scripts/repoctl <leaf> --json`, intentionally omitting required inputs.

- 70/70 returned valid JSON.
- 70/70 returned exactly `ok`, `command`, `data`, `problems`, `warnings`, and `next_actions`.
- 48 minimal calls failed at parse time.
- 48/48 parse failures returned `next_actions: []`.
- Parse failures identified only the parent group, such as `task`, `knowledge`, or `context`, instead of the intended leaf.
- Success payloads mix dotted identities (`task.finish`, `repo.list`) and space-separated identities (`meta status`, `field-gate run`).
- Exception payloads derive a dotted identity independently from handler literals.

Envelope shape is therefore stable, but failure recovery and command identity are not.

[Source: `docs/contracts/repoctl-json-contract.md`; `tools/repoctl/cli.py`; 70-leaf extracted-artifact smoke executed in this Director step]

### Contract and test lock-in

The following unusable behavior is not accidental drift; contracts and tests actively preserve it:

1. `next_actions` commands may contain unresolved placeholders and are explicitly allowed to be incomplete.
2. Structured verification remains nonpassing when any historical record for the same current subject/version was failed, mixed, or blocked, even after a later passed rerun.
3. Handoff generation writes both a task-carried digest commitment and a separate origin sidecar before a separate binding receipt is considered.
4. The actual-scope test intentionally expects doctor to report `finish_ready: true` before finish rejects the same unchanged state.
5. Block and cancel tests require completion-shaped Verification input instead of a transition reason.
6. Ambiguous resume tests require candidate inspection actions but provide no command that selects a candidate for resume.

[Source: `docs/contracts/repoctl-json-contract.md`; `docs/contracts/repoctl-discovery-outcome-contract.md`; `docs/contracts/repoctl-context-contract.md`; `tests/repoctl/task/test_task_finish.py`; `tests/repoctl/task/test_task_lifecycle.py`; `tests/repoctl/task/test_task_cancel_block.py`]

### Code and persisted-state surface

- `tools/repoctl`: 43 Python modules, 57,611 lines, 1,845 functions
- `tools/repoctl/cli.py`: 7,852 lines, 220 functions
- `_next_actions_for_problems`: 471 lines
- At least 25 command-producing or command-adjacent lines contain angle-bracket placeholders
- `meta_status()` is a direct alias of `meta_inventory()`
- `knowledge candidate build` and `knowledge candidate suggest` share one branch-heavy handler
- `field-gate run release-candidate` reports `scope=workspace_control_plane` and `product_readiness=not_evaluated`
- Handoff origin sidecars, task-carried commitments, three binding schema readers, completion receipts, outcome state, Context Packs, result receipts, Graph snapshots/provider stores, completion catalogues, Knowledge projections/events, and evidence indexes coexist

The module-boundary contract says the CLI should own parser construction, presentation, envelope completion, and thin orchestration. The current CLI also owns recovery policy, release gate orchestration, upgrade postflight composition, task lifecycle health projection, command-specific compatibility shaping, and several domain transition adapters.

[Source: `docs/contracts/repoctl-module-boundaries.md`; `tools/repoctl/cli.py`; `tools/repoctl/meta.py`; `tools/repoctl/tasks.py`; AST and line census executed in this Director step]

### Tests

- 803 tests collected
- 40 test files
- 627 test functions
- 4,687 direct `assert` statements
- 17 test files reference `next_actions`
- Only two files directly exercise help rendering
- Most continuation assertions pin individual strings or problem codes rather than complete copy-paste recovery journeys

A green suite remains necessary, but it cannot override a demonstrated dead end or false readiness result.

[Source: `.venv/bin/pytest --collect-only -q`; AST test census; `rg -l next_actions tests`]

### Release and upgrade

- `pyproject.toml`, `repoctl-upgrade-manifest.json`, the extracted archive, and `repoctl version` all report `0.9.0`.
- Current HEAD is 19 commits after the `v0.9.0` commit while keeping the same version.
- A current archive contains 151 files.
- Compressed archive size: 2,355,346 bytes.
- Sum of archived file bytes: 5,588,918 bytes.
- The archive carries 54 test/fixture files totaling 1,202,513 bytes.
- It carries 43 Python test files totaling 1,159,296 bytes.
- Planning from an `origin/main` checkout to current HEAD succeeds as `0.9.0` to `0.9.0` with 125 managed operations and no conflict.
- Applying that plan succeeds; the resulting workspace still reports `0.9.0`.
- `python -m tools.repoctl.release --help` exits zero and creates `--help/<package>-<version>.tar.gz` instead of showing help.

Release identity currently does not identify shipped behavior, and the adopter payload is coupled to source-tree tests.

[Source: `pyproject.toml`; `repoctl-upgrade-manifest.json`; `tools/repoctl/release.py`; `tools/repoctl/upgrade.py`; `.github/workflows/release.yml`; `tests/test_release_repository.py`; release build/extract and same-version upgrade E2E executed in this Director step]

## Runtime journey findings

### F01 — Ambiguous resume has inspection but no selection

`AGENTS.md` requires explicit selection when multiple live tasks exist. The CLI returns bounded candidates and exact `task show <id> --summary --json` actions, but `task resume <id> --json` is not a parser path. After inspecting a candidate, the user still cannot ask repoctl to produce that candidate's resume guidance while ambiguity remains.

This blocks the mandatory session-start flow and would be made worse by introducing a persistent “current task” pointer. Selection should be an explicit, read-only argument to the existing command.

[Source: `AGENTS.md`; `tools/repoctl/cli.py`; `tests/repoctl/task/test_task_lifecycle.py::test_task_resume_exposes_only_one_current_live_handoff`; `./scripts/repoctl task resume T-20260101000000Z --json`]

### F02 — Doctor can claim readiness for a finish that is guaranteed to fail

The actual-scope test changes two files outside Chosen. `task doctor` exits zero, reports only `task_chosen_scope_drift`, and sets `finish_ready: true`. Without changing task or repository state, `task finish` then exits two with `actual_changes_outside_chosen`.

The finish error also loses the complete scope-resolution payload, so the user receives inspection but no executable resolution path.

[Source: `tests/repoctl/task/test_task_finish.py::test_task_doctor_and_finish_share_actual_scope_preflight`; `tools/repoctl/cli.py`; `tools/repoctl/tasks.py`]

### F03 — Cold Context fails despite returning useful source

In a valid product Git repository containing only `app.py` and no `.repometa`, Graph snapshot, completion catalogue, or Knowledge projection:

- Context returns `repos/app.py` in `likely_change_surface`.
- The command nevertheless exits one.
- Problems are `context_graph_unavailable`, `missing_repometa_policy`, and `context_linked_knowledge_unavailable`.
- Seven recovery actions are returned.
- The same Context rerun appears twice.
- `graph build` also fails solely on `missing_repometa_policy`.

Source, metadata enrichment, Graph materialization, history, and Reviewed Knowledge are separate evidence lanes. Optional cold lanes must not convert readable source into command failure.

[Source: `docs/contracts/repoctl-context-contract.md`; current extracted-artifact cold Context/Graph E2E; `tools/repoctl/context.py`; `tools/repoctl/graph.py`; `tools/repoctl/cli.py`]

### F04 — Block and cancel reuse finish Verification ceremony

`task block` and `task cancel` call the same Verification-input helper used by finish. A user who needs to pause or abandon work must create a completion-shaped external artifact, and repoctl replaces the Task Verification section with that transition text.

Blocker/cancellation intent must remain evidenced, but it is not a successful completion check and should not overwrite existing Verification.

[Source: `tools/repoctl/cli.py`; `tools/repoctl/tasks.py`; `tests/repoctl/task/test_task_cancel_block.py`; `AGENTS.md`]

### F05 — A later passed rerun cannot recover an earlier failed check

Structured verification is append-only, which is correct for audit history. Readiness, however, aggregates all statuses for a current subject/version and accepts only the exact set `{passed}`. A later passed rerun therefore cannot recover an earlier failure. The test suite has also been changed to avoid offering the ineffective “append a pass” action rather than fixing the gate semantics.

History should remain immutable; the active gate should use the latest applicable record.

[Source: `docs/contracts/repoctl-json-contract.md`; `docs/contracts/repoctl-discovery-outcome-contract.md`; `tools/repoctl/discovery_outcomes.py`; `tests/repoctl/task/test_task_lifecycle.py::test_task_doctor_does_not_offer_an_ineffective_pass_append_for_nonpassing_current_evidence`]

### F06 — Handoff safety is implemented through duplicate provenance systems

Generated tasks carry a Handoff digest commitment and a separate origin sidecar containing template version, a list of generated body digests, and a state digest. Binding then writes a separate receipt whose reader accepts three schema versions and binds Handoff, task contract, Discovery, Execution Log, Verification, repository observation, direct children, and optional Context Pack inputs.

The safety requirement is smaller: an unchanged generated placeholder must not bind; authored four-field text must be explicitly bound; later relevant drift must deactivate it; archived prose remains readable.

[Source: `docs/PRD.md`; `AGENTS.md`; `tools/repoctl/tasks.py`; `tests/repoctl/task/test_task_lifecycle.py`]

### F07 — Recovery actions are often shell-invalid templates

The JSON contract explicitly permits placeholder commands and incomplete actions. At least 25 command-producing or adjacent lines contain `<query>`, `<path>`, `<id>`, `<kind>`, `<claim>`, or similar unresolved values. Bash interprets unquoted angle brackets as redirection, so these strings are not copy-paste commands.

Examples also invent `/tmp` evidence paths that do not exist. Candidate-bearing Graph, Knowledge, task, and repository results do not consistently turn their concrete candidate data into exact replay actions.

[Source: `docs/contracts/repoctl-json-contract.md`; `tools/repoctl/cli.py`; `bash -lc "./scripts/repoctl repo adopt <candidate-path> --id <stable-repo-id> --json"`; placeholder census executed in this Director step]

### F08 — Parser, help, identity, and semantics are separate models

- `version` works only through raw argument inspection and has no help path.
- Success and failure use different command-identity derivations.
- `meta status` and `meta inventory` expose two concepts over the same provider; verbose status already supplies the inventory files.
- `knowledge candidate suggest` is a branch of candidate build rather than an independent operation.
- `field-gate run release-candidate` is a repoctl control-plane gate, not product release readiness.
- 26 visible leaves are absent from operator documentation.

Users currently have to learn parser spellings, handler literals, JSON identities, docs coverage, and actual semantics separately.

[Source: `tools/repoctl/cli.py`; `tools/repoctl/meta.py`; parser/help census; documentation census; field-gate runtime output]

### F09 — Release identity and builder behavior do not identify the payload

Current HEAD can apply 125 managed changes to the tagged `0.9.0` checkout while both sides continue reporting `0.9.0`. The archive unnecessarily carries Python test source. The release builder itself treats `--help` as a filesystem output directory and mutates the working directory while claiming success.

This prevents operators and automation from distinguishing tagged behavior from post-tag behavior and expands the adopter upgrade surface without runtime benefit.

[Source: current release build/extract E2E; same-version upgrade E2E; `tools/repoctl/release.py`; `repoctl-upgrade-manifest.json`; `.github/workflows/release.yml`]

## Direct repairs retained or applied in this Director step

These defects were small and local, so they were fixed instead of delegated:

1. Parse-time failures now recognize `--format json` as JSON intent for the current version.
2. `task list --json` returns nonzero when it reports lifecycle errors.
3. `repo list --json` emits one exact adopt command per valid detected repository candidate.
4. Missing finish Verification no longer recommends blindly rerunning the same finish command.
5. Missing Graph state points to ordinary `graph build`, not unconditional rebuild.
6. Task creation no longer emits redundant Discovery mutations before evidence exists.
7. Ambiguous resume emits exact candidate inspection commands.
8. Successful structured-verification recording points to `task doctor` so readiness can be observed.
9. Successful finish no longer recommends an optional Knowledge-candidate preview.
10. Release tests and workspace checks run for the current ref even when a release already exists, and every existing tag is verified against the current commit rather than only when the GitHub Release is absent.

Direct-repair validation is part of the final evidence section below.

## Issued implementation packets

Packets are ordered. Later packets must rebase on the deletions and contract decisions of earlier packets. Additional files require Director approval. None of these packets may add persisted state unless its Acceptance Criteria explicitly require it; no current packet does.

---

## RCTL-U01 — Add explicit, stateless resume selection

**Priority:** P0

**Domain Expert Key:** `repoctl-resume-selection`

### Problem reproduction commands

```bash
./scripts/repoctl task resume T-20260101000000Z --json

.venv/bin/pytest -q \
  tests/repoctl/task/test_task_lifecycle.py::test_task_resume_exposes_only_one_current_live_handoff \
  -vv
```

The first command fails at parse time. The test passes while preserving an ambiguous result whose only continuations inspect candidates without selecting one.

### Concrete goal

Make `task resume [TASK_ID]` the explicit, read-only selection boundary. With no argument, keep deterministic `no_live | single_live | ambiguous` discovery. With an ID, produce the same Handoff and lifecycle guidance that the selected live task would receive in a single-live workspace.

### Allowed files

- `tools/repoctl/cli.py`
- `tools/repoctl/tasks.py`
- `AGENTS.md`
- `README.md`
- `docs/tasks/README.md`
- `docs/contracts/repoctl-context-contract.md`
- `docs/contracts/repoctl-json-contract.md`
- `tests/repoctl/task/test_task_lifecycle.py`
- `tests/repoctl/test_cli_discoverability.py`

### Non-goals

- adding a persistent current-task pointer, selection receipt, environment setting, or config field
- auto-starting, auto-binding, or mutating the selected task
- selecting an archived or terminal task for execution
- falling back to archived history when no live task exists
- weakening Handoff or repository-health checks

### Actual CLI runtime Acceptance Criteria

1. With two live tasks, bare `task resume --json` remains nonzero and returns bounded candidates.
2. Each candidate has one exact `task resume <ID> --json` continuation; an optional `task show` action may remain for inspection but is not the only path.
3. `task resume <live-ID> --json` succeeds in the same ambiguous workspace and returns that task's full `resume_guidance` without writing any file.
4. Selecting a missing, malformed, archived, or terminal ID produces canonical `task.resume` failure identity, a typed reason, and an executable inspection/recovery action where recovery exists.
5. Single-live and explicit-selection projections are byte-equivalent after removing selection metadata and timestamps, if any.
6. `git status --short` is unchanged before and after every resume variant.

### Deletion and simplification criteria

- Reuse one resume-guidance builder for single-live and explicit selection.
- Delete the inspect-only dead end; do not add a second selection command.
- Add no persisted selector state, migration, schema, or compatibility alias.
- Net production code may grow only by the parser/selection branch that replaces duplicated candidate handling.

---

## RCTL-U02 — Make doctor and finish share one closure preflight

**Priority:** P0

**Domain Expert Key:** `repoctl-closure-parity`

### Problem reproduction command

```bash
.venv/bin/pytest -q \
  tests/repoctl/task/test_task_finish.py::test_task_doctor_and_finish_share_actual_scope_preflight \
  -vv
```

The test passes while preserving false readiness.

### Concrete goal

Build one closure-preflight result used by both `task doctor` and `task finish`. Doctor may report advisory information, but `finish_ready` must be false whenever the next finish call would fail without an intervening task or repository change. Both paths must preserve complete actionable blocker data.

### Allowed files

- `tools/repoctl/cli.py`
- `tools/repoctl/tasks.py`
- `tools/repoctl/discovery_outcomes.py`
- `docs/contracts/repoctl-json-contract.md`
- `docs/contracts/repoctl-discovery-outcome-contract.md`
- `tests/repoctl/task/test_task_finish.py`
- `tests/repoctl/task/test_task_lifecycle.py`

### Non-goals

- weakening any finish mutation gate
- auto-adding changed paths to Chosen
- auto-reverting product changes
- treating generated files as harmless without an explicit repository rule
- adding a persisted preflight receipt or cache

### Actual CLI runtime Acceptance Criteria

1. With actual changed paths outside Chosen, doctor exits nonzero, sets `finish_ready: false`, and returns the complete sorted path set at `data.action_inputs.unchosen_actual_paths`.
2. Doctor returns one scope-resolution decision with existing choices `add_to_chosen | revert_change | move_to_follow_up` and at most one optional inspection action.
3. If finish is attempted unchanged, it returns the same blocker code, path set, and resolution decision; exception shaping must not discard domain data.
4. After one valid resolution, doctor becomes ready and finish succeeds without unrelated ceremony.
5. Every finish-hard blocker has a parity test proving doctor rejects the same unchanged state.
6. No state is written by doctor.

### Deletion and simplification criteria

- One domain result owns closure blockers and action inputs.
- Delete the separate drift-only readiness branch or merge it into that result.
- Do not add a replacement readiness field beside `finish_ready`; correct the existing field.
- Net production code for closure preflight must not grow after duplicate checks are removed.

---

## RCTL-U03 — Decouple Context and Graph bootstrap lanes

**Priority:** P0

**Domain Expert Key:** `repoctl-context-bootstrap`

### Problem reproduction command

```bash
tmp="$(mktemp -d)"
git archive HEAD | tar -x -C "$tmp"
mkdir "$tmp/repos"
git -C "$tmp/repos" init -q
git -C "$tmp/repos" config user.email audit@example.com
git -C "$tmp/repos" config user.name audit
printf 'def run():\n    return 1\n' > "$tmp/repos/app.py"
git -C "$tmp/repos" add app.py
git -C "$tmp/repos" commit -qm initial
(cd "$tmp" && ./scripts/repoctl context query 'where is run' --repo-id main --json)
(cd "$tmp" && ./scripts/repoctl graph build --repo-id main --json)
```

Context currently finds `repos/app.py` but exits one with three cold-lane problems and seven actions. Graph build fails solely because metadata is absent.

### Concrete goal

Treat current source, metadata enrichment, Graph, completion history, and Reviewed Knowledge as independent evidence lanes. Readable current source must produce a successful partial Context result. Graph must materialize source/import evidence without requiring metadata initialization.

### Allowed files

- `tools/repoctl/context.py`
- `tools/repoctl/context_sources.py`
- `tools/repoctl/context_retrieval.py`
- `tools/repoctl/graph.py`
- `tools/repoctl/graph_store.py`
- `tools/repoctl/meta.py`
- `tools/repoctl/cli.py`
- `docs/contracts/repoctl-context-contract.md`
- `docs/contracts/repoctl-graph-contract.md`
- `docs/contracts/repoctl-json-contract.md`
- `tests/repoctl/context/test_context_query.py`
- `tests/repoctl/graph/test_graph_build.py`
- `tests/repoctl/graph/test_graph_query.py`
- `tests/repoctl/workspace/test_check_validation.py`

### Non-goals

- adding a setup wizard or bootstrap command
- adding persisted bootstrap state or readiness markers
- silently claiming Graph, metadata, history, or Knowledge completeness
- auto-running metadata, Graph, history, or Knowledge mutations
- changing repository selection rules
- downgrading corrupt authoritative state to absence

### Actual CLI runtime Acceptance Criteria

1. The Context reproduction exits zero, returns `repos/app.py`, and reports cold optional lanes through warnings/completeness rather than command-failing problems.
2. `graph build --repo-id main --json` succeeds without `.repometa`, materializes available source/import evidence, and marks metadata enrichment unavailable.
3. Cold Context returns one ordered and deduplicated recovery sequence with no more than three actions; the same query rerun appears at most once and only after prerequisites.
4. Initializing metadata, history, or Knowledge later enriches the result but is not required to read source.
5. Missing repository identity, unreadable source, invalid persisted Graph state, or corrupt authoritative records remain typed failures.
6. Context and Graph builds do not create optional stores they were not explicitly asked to create.

### Deletion and simplification criteria

- Remove hard-error promotion whose sole cause is absence of an optional lane.
- Remove duplicate recovery aggregation between Context completeness and `_next_actions_for_problems`.
- Add no persisted schema, bootstrap marker, or setup receipt.
- Cold-query control flow must have fewer cross-lane branches after the change.

---

## RCTL-U04 — Give block and cancel a transition-intent contract

**Priority:** P0

**Domain Expert Key:** `repoctl-task-transition-intent`

### Problem reproduction command

```bash
.venv/bin/pytest -q tests/repoctl/task/test_task_cancel_block.py -vv
```

The passing tests require external completion-shaped Verification files and replace the Task Verification section.

### Concrete goal

Model block and cancel as explicit transition intent with a required reason. Preserve existing Verification byte-for-byte. Keep dirty-cancel ownership evidence independent from the transition reason.

### Allowed files

- `tools/repoctl/cli.py`
- `tools/repoctl/tasks.py`
- `AGENTS.md`
- `docs/README.md`
- `docs/tasks/README.md`
- `docs/contracts/repoctl-json-contract.md`
- `tests/repoctl/task/test_task_cancel_block.py`
- `tests/repoctl/task/test_task_lifecycle.py`

### Non-goals

- allowing reasonless block or cancel
- weakening dirty-cancel repository evidence
- auto-reverting product changes
- treating blocked as terminal
- rewriting user-authored Verification or Handoff prose
- adding blocker/cancellation sidecars or receipts

### Actual CLI runtime Acceptance Criteria

1. `task block T --reason 'waiting for upstream API' --json` succeeds for a doing task without an external Verification artifact.
2. `task cancel T --reason 'superseded by T-new' --json` archives a clean task without finish Verification.
3. The reason is visible in the transition result and task history/closure while the pre-existing Verification section is byte-identical.
4. Dirty cancel still fails unless the existing explicit dirty-cancel choice is supplied; the residue set remains complete.
5. Block retains the existing authored Handoff and stays on the Board.
6. Failure output contains an executable command or an explicit decision action, never an invented `/tmp` file.

### Deletion and simplification criteria

- Remove `--verification-file` from block and cancel.
- Delete their branches through `_verification_input_arg`.
- Reuse one reason parser/domain helper for both transitions.
- Add no transition schema, sidecar, or compatibility alias in the new minor release.
- The combined block/cancel production path must become shorter.

---

## RCTL-U05 — Let the latest applicable verification decide readiness

**Priority:** P1

**Domain Expert Key:** `repoctl-verification-lifecycle`

### Problem reproduction command

```bash
.venv/bin/pytest -q \
  tests/repoctl/task/test_task_lifecycle.py::test_task_doctor_does_not_offer_an_ineffective_pass_append_for_nonpassing_current_evidence \
  -vv
```

### Concrete goal

Keep immutable verification history, but compute the active gate from the last appended record applicable to each current subject/version. A passed rerun must recover an earlier failure; a later failure must make the gate nonpassing again.

### Allowed files

- `tools/repoctl/discovery_outcomes.py`
- `tools/repoctl/cli.py`
- `docs/contracts/repoctl-discovery-outcome-contract.md`
- `docs/contracts/repoctl-json-contract.md`
- `tests/repoctl/task/test_task_lifecycle.py`
- `tests/repoctl/task/test_task_finish.py`

### Non-goals

- deleting, rewriting, or hiding historical failed evidence
- introducing a mutable current-verification sidecar
- allowing a pass for a stale subject version to satisfy the current version
- inferring status from prose or command text
- changing subject identity or evidence digest rules

### Actual CLI runtime Acceptance Criteria

1. A failed record for a current subject/version makes doctor non-ready and names that subject.
2. A later passed record for the same subject/version makes doctor ready when no other blocker remains.
3. Full/audit output preserves both records in append order.
4. A later failed record after a pass makes the gate nonpassing again.
5. Finish and doctor consume the same latest-record projection.
6. Records for other versions or subjects never supersede the current subject/version.

### Deletion and simplification criteria

- Replace aggregate status-set equality with one latest-applicable-record selection.
- Delete branches whose only purpose is treating every historical nonpass as permanently active.
- Add no supersession event, mutable pointer, or new persisted field.
- Net production code must not grow.

---

## RCTL-U06 — Collapse Handoff provenance to one marker and one binding

**Priority:** P1

**Domain Expert Key:** `repoctl-handoff-state-reduction`

### Problem reproduction command

```bash
.venv/bin/pytest -q tests/repoctl/task/test_task_lifecycle.py \
  -k 'handoff_origin or origin_unknown or generated_handoff or resume_binding' \
  -vv
```

### Concrete goal

Preserve Handoff safety with one explicit generated marker in the task/template and one current binding receipt. Reject a generated placeholder, validate authored four-field text at bind time, bind it to current task/repository inputs, and deactivate it on relevant drift. Keep archived Handoffs readable without a second provenance sidecar or digest registry.

### Allowed files

- `tools/repoctl/tasks.py`
- `tools/repoctl/cli.py`
- `docs/tasks/TEMPLATE.md`
- `docs/tasks/PARENT_TEMPLATE.md`
- `AGENTS.md`
- `README.md`
- `docs/tasks/README.md`
- `docs/contracts/repoctl-context-contract.md`
- `docs/contracts/repoctl-json-contract.md`
- `tests/repoctl/task/test_task_create.py`
- `tests/repoctl/task/test_task_lifecycle.py`
- `tests/repoctl/context/test_context_pack.py`
- `tests/repoctl/test_upgrade.py`

### Non-goals

- auto-binding on create, start, show, or resume
- executing or semantically interpreting Handoff commands
- allowing unchanged generated placeholder text to bind
- discarding readable archived Handoffs
- weakening repository-observation or bound-Pack drift checks
- adding a replacement provenance sidecar or migration ledger

### Actual CLI runtime Acceptance Criteria

1. A new task carries exactly one explicit generated marker and creates no Handoff-origin sidecar.
2. Bind rejects the generated marker and succeeds only after all four Handoff fields are authored and the marker is removed through the supported edit path.
3. One successful bind makes the Handoff current; execution still additionally requires healthy lifecycle evidence.
4. Editing Handoff, task contract, Discovery, Verification, bound Pack, direct-child lifecycle, or observed repository state deactivates the binding with bounded reason codes.
5. A legacy unmarked live task requires one explicit fresh bind but no origin-sidecar migration.
6. The immediately preceding `0.9.0` binding remains readable through one isolated compatibility reader; older binding schema branches are removed.
7. Archived Handoffs remain readable historical evidence.

### Deletion and simplification criteria

- Delete `handoff-origins/**`, its schema validator/writer, generated digest lists, and task-carried digest commitment generation.
- Delete at least two old binding-schema readers.
- Reduce public freshness states to `current`, `inactive`, and `historical`; inactive reason codes retain diagnosis.
- Add no replacement provenance store, migration event, or frozen-template comparison.
- Production plus tests must show a material net line reduction.

---

## RCTL-U07 — Unify parser, help, identity, and command semantics

**Priority:** P1

**Domain Expert Key:** `repoctl-cli-surface`

### Problem reproduction commands

```bash
./scripts/repoctl --help
./scripts/repoctl version --help
./scripts/repoctl context query --help
./scripts/repoctl meta status --json
./scripts/repoctl meta inventory --json
./scripts/repoctl field-gate run release-candidate --json
```

### Concrete goal

Define every public command once and derive argparse help, canonical dotted JSON identity, and handler wiring from that definition. Remove duplicate command concepts while preserving capabilities:

- make `version` an ordinary parser command
- keep only `--json` for machine output
- fold `meta inventory` into `meta status --verbose`
- fold `knowledge candidate suggest` into `knowledge candidate build --from-task ... --dry-run`
- name the control-plane field gate `repoctl-release`, not generic `release-candidate`
- document every visible leaf or hide it as an internal diagnostic

### Allowed files

- `tools/repoctl/cli.py`
- `tools/repoctl/meta.py`
- `docs/contracts/repoctl-json-contract.md`
- `README.md`
- `docs/README.md`
- `docs/tasks/README.md`
- `docs/workflows/repoctl-upgrade.md`
- `tests/repoctl/test_cli_discoverability.py`
- command-specific tests whose public command identity or removed alias changes

### Non-goals

- introducing a new CLI framework or dependency
- generating a documentation website or code generator
- retaining removed compatibility aliases in the new minor release
- changing domain behavior unrelated to command identity or duplicate surface
- exposing internal benchmark leaves merely to satisfy documentation coverage

### Actual CLI runtime Acceptance Criteria

1. Root and every visible group list each subcommand with a one-line purpose.
2. Every positional and required option has actionable help, including accepted values or where to obtain the input.
3. `version --help`, `version`, and `version --json` are ordinary argparse paths.
4. Success, domain failure, and parse failure for one command use the same dotted identity, such as `task.finish`.
5. `--json` is the only machine-output switch; `--format` accepts only human `text | markdown` where formatting is useful.
6. `meta inventory` and `knowledge candidate suggest` are absent from help; their capabilities work through the retained commands.
7. The field gate is visibly and structurally named `repoctl-release`, and its output continues to state that product readiness is not evaluated.
8. Every remaining visible leaf appears in one operator command reference; internal diagnostics are hidden from normal help.
9. Internal handler names and dispatch branches match the public commands they implement.

### Deletion and simplification criteria

- Delete the raw-argv version special case.
- Delete `--format json` and its separate JSON-intent path.
- Delete mixed space/dotted command literals in handlers.
- Delete the `meta inventory` parser/handler wrapper and the `knowledge candidate suggest` branch/leaf.
- Delete the generic `release-candidate` field-gate spelling without adding an alias.
- Add no command registry abstraction larger than the parser/help/identity code it replaces.
- Parser/identity production lines must decrease or remain flat after duplicate leaves are removed.

---

## RCTL-U08 — Make every public continuation executable or explicitly non-command

**Priority:** P1

**Domain Expert Key:** `repoctl-cli-continuations`

### Problem reproduction commands

```bash
bash -lc "./scripts/repoctl repo adopt <candidate-path> --id <stable-repo-id> --json"

rg -n 'command=.*<[^>]+>|"command".*<[^>]+>|next_command.*<[^>]+>' \
  tools/repoctl/cli.py
```

### Concrete goal

Reserve every public `command` or `next_command` field for a complete copy-paste-safe command. A user-owned decision must use the existing `kind`, `source`, `target_ref`, `path`, and `choices` fields without pretending to be executable. Candidate-bearing results must derive exact replay commands from concrete candidate data.

### Allowed files

- `tools/repoctl/cli.py`
- `docs/contracts/repoctl-json-contract.md`
- `tests/repoctl/test_cli_discoverability.py`
- `tests/repoctl/graph/test_graph_query.py`
- `tests/repoctl/knowledge/test_knowledge_candidates.py`
- `tests/repoctl/knowledge/test_knowledge_lifecycle.py`
- `tests/repoctl/repository/test_repository_adoption.py`
- `tests/repoctl/task/test_task_create.py`
- `tests/repoctl/task/test_task_finish.py`
- `tests/repoctl/task/test_task_lifecycle.py`
- `tests/repoctl/context/test_context_query.py`
- `tests/repoctl/test_upgrade.py`

### Non-goals

- adding a new action schema version
- adding an argv array, template-command field, shell field, or command executor
- embedding unbounded candidate or path lists in command strings
- guessing user ownership, scope, reviewer labels, claims, or filenames
- adding a wizard

### Actual CLI runtime Acceptance Criteria

1. No public `command` or `next_command` contains `<...>`, shell redirection placeholders, invented `/tmp` evidence files, or unresolved synthetic IDs.
2. Every parse-time JSON failure returns the exact leaf identity and an exact `<leaf> --help` command.
3. Ambiguous repository, task, Graph, and other bounded candidates return one exact inspect/replay action per candidate.
4. A reviewable Knowledge candidate returns exact inspection plus explicit approve/reject decision actions; it does not fabricate reviewer labels or note files.
5. Scope and baseline decisions expose complete response-owned target lists and enum choices without a fake shell command.
6. Context recovery is ordered and unique, with at most one rerun of the same query.
7. Every emitted command is exercised in a subprocess journey: it must parse, and it must either succeed or return a domain failure attributable to current state rather than unresolved input.
8. Terminal success may legitimately return no action.

### Deletion and simplification criteria

- Delete every placeholder command string.
- Shrink `_next_actions_for_problems`; handlers with complete domain inputs own exact continuations.
- Delete duplicate problem-code branches that produce the same recovery.
- Add no parallel command representation.
- `_next_actions_for_problems` must be materially shorter than the current 471 lines.

---

## RCTL-U09 — Publish a distinct `0.10.0` release and reduce the payload

**Priority:** P1, last packet

**Domain Expert Key:** `repoctl-release-engineering`

### Problem reproduction commands

```bash
tmp="$(mktemp -d)"
mkdir "$tmp/target"
git archive origin/main | tar -x -C "$tmp/target"
./scripts/repoctl upgrade plan \
  --workspace-root "$tmp/target" \
  --from /mnt/data/workspace/git/agent-handoff-template \
  --output "$tmp/plan.json" \
  --json

python -m tools.repoctl.release --help
```

The first operation currently plans 125 same-version changes. The second creates an archive under a directory literally named `--help`.

### Concrete goal

Publish the post-`v0.9.0` contract as `0.10.0`, reject same-version managed-content drift, make the release builder an ordinary CLI, and distribute only adopter runtime/contracts/templates plus fixture assets directly consumed by shipped field-gate commands.

### Allowed files

- `pyproject.toml`
- `repoctl-upgrade-manifest.json`
- `README.md`
- `docs/workflows/repoctl-upgrade.md`
- `tools/repoctl/release.py`
- `tools/repoctl/upgrade.py`
- `.github/workflows/release.yml`
- `tests/test_release_repository.py`
- `tests/repoctl/test_upgrade.py`

### Non-goals

- publishing or pushing a tag from this task
- changing adopter-owned preservation paths without a required migration
- weakening checksums, conflict checks, plan binding, rollback, or postflight validation
- adding a same-version force flag
- bundling another installer or package manager
- deleting source-tree tests from the development checkout

### Actual CLI runtime Acceptance Criteria

1. `repoctl version`, `pyproject.toml`, the manifest, README migration copy, archive name, and expected release tag all agree on `0.10.0`.
2. Planning from a target that already reports `0.10.0` but has a different managed-content identity fails with a typed problem and performs no write.
3. No same-version override or compatibility alias exists.
4. Planning from tagged `0.9.0` to `0.10.0` succeeds; apply succeeds in a disposable adopter workspace; fresh-process postflight is clean or reports only fixture-specific non-applicability.
5. Release CI always runs tests and contract checks for the current ref and accepts an existing tag only when it resolves to that ref.
6. `python -m tools.repoctl.release --help` prints usage, exits zero, and creates no file or directory.
7. The source checkout retains all tests, but the default archive contains no Python test modules. Only field-gate fixture assets with a demonstrated runtime consumer remain.
8. The archive contains fewer than 151 files and fewer than 5,588,918 uncompressed file bytes.
9. Archive reproducibility, path safety, manifest coverage, extracted-runtime smoke, upgrade, rollback, and postflight tests pass.

### Deletion and simplification criteria

- Remove `tests/repoctl/**` from the adopter-managed payload.
- Keep only fixture files directly read by shipped field-gate commands.
- Delete same-version apply behavior rather than adding a drift mode.
- Delete the positional-output-directory parsing shortcut in `release.py` in favor of a minimal argparse parser.
- Delete existing-tag skip branches; one release flow verifies the current ref before publication short-circuits.
- The managed file count and byte count must both decrease from the measured baseline.

## Required implementation order

1. RCTL-U01 — stateless resume selection
2. RCTL-U02 — closure parity
3. RCTL-U03 — Context/Graph lane decoupling
4. RCTL-U04 — transition intent
5. RCTL-U05 — latest applicable verification
6. RCTL-U06 — Handoff state reduction
7. RCTL-U07 — unified CLI surface
8. RCTL-U08 — executable continuations
9. RCTL-U09 — release identity and payload

U09 is last because it publishes the intentional minor-version command and contract breaks only after preceding deletions stabilize.

## Director-step validation evidence

Commands executed during this step include:

```bash
./scripts/repoctl task resume --json
./scripts/repoctl task resume T-20260101000000Z --json
./scripts/repoctl version --help
./scripts/repoctl version --format json
./scripts/repoctl field-gate run release-candidate --json
./scripts/repoctl meta status --json
./scripts/repoctl meta inventory --json

# Recursive argparse census and 89 real --help invocations
# 70-leaf extracted-artifact --json smoke
# Current operator-documentation coverage census
# Placeholder-command census
# AST production/test census

.venv/bin/pytest --collect-only -q
# 803 tests collected

.venv/bin/pytest -q \
  tests/repoctl/graph/test_graph_query.py \
  tests/repoctl/repository/test_repository_adoption.py \
  tests/repoctl/task/test_task_finish.py \
  tests/repoctl/task/test_task_lifecycle.py \
  tests/repoctl/test_cli_discoverability.py \
  tests/repoctl/workspace/test_check.py \
  tests/repoctl/workspace/test_check_validation.py \
  tests/test_release_repository.py
# 197 passed in 74.67s

.venv/bin/pytest -q
# 803 passed in 675.39s

.venv/bin/python -m tools.repoctl.release <temporary-dist>
# Build, inspect, extract, and execute current archive

# Fresh source-only repository Context and Graph E2E
# origin/main -> current same-version upgrade plan/apply E2E
# release builder --help side-effect reproduction

git diff --stat
# 10 tracked files changed, 81 insertions, 26 deletions
# The Director review remains a separate untracked implementation packet.

git diff --check
# No whitespace errors.

./scripts/repoctl check --json
# ok=true; problems=[]; warnings=[]
```

All direct repairs are covered by the targeted set above. The full suite passing is regression evidence only; it does not override the runtime dead ends demonstrated in F01–F09.

## Handoff constraints for implementers

- Do not add persisted state to solve these packets.
- Prefer deleting branches, sidecars, schema readers, aliases, duplicate flags, placeholder commands, and release payload entries.
- Preserve fail-closed behavior at trust boundaries, corrupt authoritative state, repository escape, and mutation gates; remove fail-closed coupling between independent optional lanes.
- Treat a passing unit suite as necessary but insufficient. Each packet closes only after its stated public CLI journey passes.
- Record net production/test line changes and deleted concepts when each packet closes.
