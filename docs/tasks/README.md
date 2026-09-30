# Tasks

This directory contains live Task files and the canonical creation templates. Standalone done or canceled Tasks move to `docs/archive/tasks/`; completed child files may remain here until their parent closes.

Create product work with:

```bash
./scripts/repoctl task create --start --json "Task title"
```

This shorthand selects the only configured product repository. For explicitly tracked root-only work, use `task create --area docs --start --slug reconcile-workspace-docs "Reconcile workspace docs" --json` without `--repo-id` or `--repo-ref`; `--area ops` is also root-only when no repository is selected. `--area docs --repo-id main` selects product documentation. Root control-plane edits ordinarily need no Task (see `AGENTS.md`).

Use `--type parent` only to coordinate independently verifiable repo-scoped child Tasks. Use `backlog add/list/show/remove` for deferred prose, then create an explicit Task with `--backlog-id` when work starts. Repoctl never derives area, files, expected behavior, or checks from Backlog text.

## Current scope

For repo-scoped work, `## Discovery` contains one authoritative Chosen set plus optional Notes:

```bash
./scripts/repoctl task discovery add T-... \
  --chosen repos/src/owner.py \
  --chosen repos/tests/test_owner.py \
  --note "owner and its direct behavior check" \
  --json
```

Chosen values must be canonical workspace-relative files inside the selected product repository. Add files to the current set with `--chosen`. Replace the set with `--replace-chosen ... --reason "..."` when scope changes. A `todo` Task may record scope before start; a `doing` or `blocked` Task needs a current matching start baseline.

Context, Graph, Git, metadata, and direct reads help decide scope. Their queries, candidate lists, opened files, and result selections are not recorded as separate Task state. Legacy Candidate/Reviewed/Excluded fields and outcome JSON remain readable but are not written by the current command flow.

## Work notes and Handoff

`## Verification` is optional free-form text. Keep a command or result only when it helps a future reader. Repoctl does not parse, grade, register, hash, or require it.

Use `task log append` for short timestamped checkpoints. Before a real pause or transfer, replace the generated Handoff placeholder with four concrete fields, remove its marker, and bind it:

```bash
./scripts/repoctl task handoff bind T-... --json
```

The fields are Next exact step, First file to open, First command to run, and Done when. The command is inert text. A later Task, Handoff, Chosen/Notes, log, optional Verification, child, or repository change makes the binding inactive. Context Packs are one-time reference views and are not bindable.

## Finish and history

Finish directly after the work is ready:

```bash
./scripts/repoctl task finish T-... --json
```

Finish checks repository selection, the task-start baseline, pre-existing dirty ownership, actual changes against Chosen, and metadata for changed files. It does not check whether Verification text exists or whether a test passed. If task changes were committed after start, use `task doctor --use-committed-diff` and finish with the same flag.

Block and cancel need explicit intent through `--reason` or `--reason-file`. Completed Tasks and completion receipts are immutable; create later work with `task create --follow-up-of T-old`. Receipt hashes bind lifecycle history to the stored Task artifact and do not represent test proof.

Cancel creates no completion receipt. A canceled child leaves Board but stays in `docs/tasks/` until its parent closes; inspect its frontmatter and Closure instead of asking for a nonexistent archive or receipt. A finish receipt records lifecycle/change identity, while tests, product acceptance, and Git delivery need their own observed evidence. No Handoff binding is required solely to finish uninterrupted work.

Repoctl alone mutates Board membership, lifecycle frontmatter, start baselines, ownership decisions, Handoff bindings, completion receipts, archive locators, and archive transitions. Humans and agents own the meaning of Goal, Chosen/Notes, Execution Log, optional Verification, and Handoff.
