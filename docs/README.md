# Workspace Control Docs

Root `docs/**` contains the private workspace ledger, repoctl contracts and workflows, and adopter-owned project context.

## Contents

- `BOARD.md` — live task registry; Task frontmatter remains status authority.
- `tasks/` — live tasks and canonical creation templates.
- `archive/tasks/` — completed and canceled standalone task records.
- `contracts/` — repoctl JSON, Context, Graph, debug, and module contracts.
- `workflows/` — reusable operating procedures.
- `knowledge/records/` — reusable decisions, invariants, and failure modes.
- `knowledge/events/` — preserved migration input from older releases; current commands do not interpret it.
- `PRD.md` — adopter-owned project context seed.
- `REPOS.md` — optional human repo map for multi-repository workspaces.

Keep product-public documentation in the relevant repository under `repos/**`. Large private project context may be split under root `docs/prd/` with `docs/PRD.md` as its index.

## Common commands

```bash
# Current work
./scripts/repoctl task create --start --json "Task title"
./scripts/repoctl task resume --json
./scripts/repoctl task show T-... --json
./scripts/repoctl task discovery add T-... --chosen repos/path --note "why this is in scope" --json
./scripts/repoctl task log append T-... "meaningful checkpoint" --json
./scripts/repoctl task handoff bind T-... --json
./scripts/repoctl task doctor T-... --json
./scripts/repoctl task finish T-... --json

# Explicit transitions and follow-ups
./scripts/repoctl task block T-... --reason "waiting for upstream API" --json
./scripts/repoctl task cancel T-... --reason "superseded by T-new" --json
./scripts/repoctl task create --follow-up-of T-old --slug follow-up "Follow-up title" --json

# Repository understanding
./scripts/repoctl meta check --repo-id main --json
./scripts/repoctl graph build --repo-id main --json
./scripts/repoctl graph query --repo-id main --file path --json
./scripts/repoctl context query "question" --repo-id main --json
./scripts/repoctl context pack --task T-... --repo-id main --json

# Reusable reasoning
./scripts/repoctl knowledge add --repo-id main --kind decision \
  --claim "reusable conclusion" --reason "why it applies" \
  --source docs/adr/example.md --json
./scripts/repoctl knowledge query "question" --repo-id main --json
./scripts/repoctl knowledge show K-... --repo-id main --json
./scripts/repoctl knowledge rebuild --repo-id main --json

# Maintenance
./scripts/repoctl check --json
./scripts/repoctl check --audit-history --json
./scripts/repoctl history rebuild --repo-id main --json
./scripts/repoctl upgrade status --json
```

Use `/tmp` for disposable Context Pack exports, review notes, and other temporary files. A saved Pack is still reference material and is never bound to Task or Handoff state.

`## Verification` is optional free-form Task text. There is no verification registration command or external verification-file finish input. Run useful checks as ordinary development work and keep only the notes a future reader needs.

## Visible command reference

Run any leaf with `--help` for exact inputs.

| Surface | Visible command leaves |
|---|---|
| Root | `version`, `check`, `debug summary` |
| Field gates | `field-gate run`, `field-gate compare` |
| Repositories | `repo list`, `repo show`, `repo check`, `repo adopt` |
| Tasks | `task create`, `task list`, `task resume`, `task show`, `task doctor`, `task log append`, `task handoff bind`, `task discovery add`, `task baseline resolve`, `task start`, `task finish`, `task block`, `task cancel` |
| Backlog | `backlog add`, `backlog list`, `backlog show`, `backlog remove` |
| Metadata | `meta init`, `meta check`, `meta status`, `meta show`, `meta query`, `meta suggest`, `meta set`, `meta remove`, `meta move`, `meta exclude` |
| Evidence | `index code`, `graph build`, `graph query`, `history rebuild`, `context query`, `context pack` |
| Knowledge | `knowledge add`, `knowledge show`, `knowledge query`, `knowledge status`, `knowledge check`, `knowledge rebuild` |
| Upgrade | `upgrade status`, `upgrade plan`, `upgrade postflight`, `upgrade apply` |

## Boundaries

- `repos/` contains product Git roots and is ignored by root Git.
- Repoctl owns Board, Backlog, lifecycle/archive, baseline, Handoff binding, completion history, and `.repometa` mutations.
- Task owns current work; Handoff owns restart instructions; Context owns read-only retrieval; Knowledge owns optional reusable reasoning.
- Context and Graph output never creates Chosen scope. Repeated queries create no result receipt cache.
- Completion receipts record lifecycle and repository history. They do not certify tests.
- New Knowledge records are complete when saved. Older records and event files remain untouched until an agent migrates the useful conclusion.
- Debug mode is ignored local diagnostic state and never authority.
