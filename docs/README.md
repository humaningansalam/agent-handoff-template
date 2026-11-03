# Workspace Control Docs

Root `docs/**` contains the private workspace ledger, repoctl contracts and workflows, and adopter-owned project context.

## Contents

- `BOARD.md` — live task registry; Task frontmatter remains status authority.
- `tasks/` — live tasks and canonical creation templates.
- `archive/tasks/` — completed and canceled standalone task records.
- `contracts/` — repoctl JSON, Graph, Task evidence, debug, and module contracts.
- `workflows/` — reusable operating procedures.
- `knowledge/records/` — reusable decisions, invariants, and failure modes.
- `PRD.md` — adopter-owned project context seed.
- `REPOS.md` — optional human repo map for multi-repository workspaces.

An ADR folder is optional and created only for an actual decision document. Existing `knowledge/events/` contains legacy migration input; current commands neither create nor interpret it. Upgrades preserve existing project records in both locations.

Keep product-public documentation in the relevant repository under `repos/**`. Root `docs/PRD.md` remains the product direction/read-map entry; add private `docs/architecture/overview.md`, `docs/specs/<capability>/requirements.md` and `design.md`, `docs/adr/`, and `docs/research/` only when useful. Move adopted material from earlier `docs/prd/**` into the latest documented structure before using it as authority.

For project understanding, follow the entry route in `AGENTS.md`: repository identity, selected product README/local guidance, active PRD index, then the source and checks relevant to the request. The command examples below are alternatives, not a checklist. Product build/test/runtime commands belong to the product contribution guide, manifests and CI; repoctl checks validate the control plane.

## Common commands

```bash
# Current work
./scripts/repoctl task create --start --json "Task title"
./scripts/repoctl task resume T-... --json
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
./scripts/repoctl graph search "question" --repo-id main --json
./scripts/repoctl graph search "current implementation and tests" --repo-id main --json

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
# Review one-time old-adopter format conversions separately before applying them:
./scripts/repoctl task migrate-receipts T-... --json
./scripts/repoctl task migrate-states T-... --json
```

Use `/tmp` for disposable investigation notes. Graph/Knowledge results are read-only evidence and never bind to Task or Handoff state.

`## Verification` is optional free-form Task text. There is no verification registration command or external verification-file finish input. Run useful checks as ordinary development work and keep only the notes a future reader needs.

## Visible command reference

Run any leaf with `--help` for exact inputs.

| Surface | Visible command leaves |
|---|---|
| Root | `version`, `check`, `debug summary` |
| Field gates | `field-gate run`, `field-gate compare` |
| Repositories | `repo list`, `repo show`, `repo check`, `repo adopt` |
| Tasks | `task create`, `task list`, `task resume`, `task show`, `task doctor`, `task log append`, `task handoff bind`, `task discovery add`, `task baseline resolve`, `task start`, `task finish`, `task block`, `task cancel`; one-time `task migrate-slop`, `task migrate-receipts`, `task migrate-states` |
| Backlog | `backlog add`, `backlog list`, `backlog show`, `backlog remove` |
| Metadata | `meta init`, `meta check`, `meta status`, `meta show`, `meta query`, `meta suggest`, `meta set`, `meta remove`, `meta move`, `meta exclude` |
| Evidence | `index code`, `graph build`, `graph query`, `history rebuild`, `graph search`, `graph workflow` |
| Knowledge | `knowledge add`, `knowledge show`, `knowledge query`, `knowledge status`, `knowledge check`, `knowledge rebuild` |
| Upgrade | `upgrade status`, `upgrade plan`, `upgrade postflight`, `upgrade apply` |

## Boundaries

- `repos/` contains product Git roots and is ignored by root Git.
- Repoctl owns Board, Backlog, lifecycle/archive, baseline, Handoff binding, completion history, and `.repometa` mutations.
- Task owns current work; Handoff owns four current restart fields; Graph offers read-only code/document evidence and navigation; Knowledge owns optional source-linked reusable reasoning.
- Graph/Knowledge reads never create Chosen scope or a per-query receipt cache; original documents and product Git roots remain authoritative.
- Completion receipts record lifecycle and repository history. They do not certify tests.
- New Knowledge records are complete when saved. Older v1 records require explicit one-time staging and finalization; unchanged original bytes remain in historical Knowledge archive after conversion.
- Debug mode is ignored local diagnostic state and never authority.
