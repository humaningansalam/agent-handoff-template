# repoctl Task Scope Contract

## Purpose

The current model keeps one scope authority: the Task's `## Discovery` section.

```markdown
## Discovery

- Chosen files:
  - `repos/src/owner.py`
  - `repos/tests/test_owner.py`
- Notes:
  - owner and direct behavior check move together
```

Chosen records intended product files. Notes keep only a scope decision or source reference useful to the next reader. Search history, opened files, candidate lists, exclusions, result selections, and check results are not current Discovery state.

## Mutation

Use:

```bash
./scripts/repoctl task discovery add T-... --chosen repos/path --json
./scripts/repoctl task discovery add T-... \
  --replace-chosen repos/new-path \
  --reason "scope moved to the actual owner" \
  --json
```

`--chosen` appends and deduplicates. `--replace-chosen` replaces the complete set and requires `--reason`; the reason is appended to Execution Log. `--note` adds a deduplicated free-form note. A call must provide at least one of those inputs.

Every Chosen value must be a canonical workspace-relative file. Repo-scoped Tasks must keep values inside their selected repository. Directories, absolute paths, traversal, backslashes, and cross-repository paths fail closed.

A `todo` Task may record Chosen before start. A `doing` or `blocked` Task may change it only when current task-start state exists for the same immutable repository selection. Done and canceled Tasks remain immutable; later scope belongs in a follow-up Task.

## Finish relationship

For product work, doctor and finish compare actual task changes with Chosen:

- actual paths outside Chosen are hard closure problems
- Chosen paths without actual changes are reported for inspection and do not invent changes
- pre-existing dirty paths remain governed by baseline ownership
- missing or placeholder Chosen blocks a repo Task that has product changes

Chosen describes scope. It is not proof that a file was read, reviewed, tested, or correct. `## Verification` is separate optional prose and does not participate in Discovery or finish readiness.

## Context and Graph

Context, Graph, Git, metadata queries, and direct source reads help an agent choose files. Their output stays read-only. Querying them creates no Task-owned result receipt, episode, candidate disposition, or future ranking signal.

A Context Pack projects current Task material when requested. It reads Chosen and Notes directly from the Task. The Pack does not write Discovery state and is not a scope authority.

Fields from retired Discovery workflows and files under `docs/tasks/.repoctl-state/discovery-outcomes/` are ignored. On the next `task discovery add`, the Task section is rewritten with Chosen and Notes only; Goal, Execution Log, Verification prose, and other Task history stay intact. An older Handoff binding is inactive until the agent reviews and binds the current Task once.

## Completion history

Task finish still publishes one immutable completion receipt and bounded history entry. New history derives changed paths and searchable human Task text without freezing a Discovery episode or structured check record. Explicit Context history modes and Graph task/artifact selectors may inspect that history; ordinary current-source ranking does not consume it as scope or authority.

Completion receipts and Task artifacts preserve history identity. They make no claim that a command or test ran.

For root-only closure blocked by changes already owned by an archived standalone
product Task, use `task doctor T-root --acknowledge-completed T-product --json`,
then `task finish` with the same option. Repeat the option only for the exact
completed Tasks being acknowledged. This reuses receipt integrity, repository
identity and baseline-to-current path lineage checks; additional drift remains
blocking. It does not change repository selection, parentage or product ownership.
Finish records the acknowledged receipt path/hash in the root Task log without
rewriting the completed Task or receipt. Product acceptance remains separate.
