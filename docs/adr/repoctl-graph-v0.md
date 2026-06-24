# ADR: repoctl Graph v0

## Status

Accepted as the historical Graph v0 authority decision.

Partially superseded for current product behavior by `docs/contracts/repoctl-graph-contract.md` and `docs/GRAPH_CONTEXT_LLMWIKI_MASTER_PLAN.md`.

## Context

repoctl already owns deterministic task lifecycle gates, explicit product repository identity, read-only code index facts, and sparse `.repometa` metadata. Future MCP and knowledge layers need a machine-facing view that combines those facts without creating a second authority.

Code index symbol values are name lists, not stable symbol identities. Import values are raw references, not resolved files, modules, packages, or symbols. Graph v0 must not pretend these facts are more precise than they are.

## Decision

Graph v0 is a read-only deterministic JSON snapshot derived from:

- repo registry `RepoTarget`
- `repoctl index code` facts
- `.repometa` policy and annotation facts
- task completion receipts
- precise provider facts when a provider supplies symbol identity and source anchors

Graph v0 node kinds:

```text
repository
file
import_ref
topic
task
change_event
artifact
symbol
anchor
```

Graph v0 edge kinds:

```text
CONTAINS
DECLARES_IMPORT
HAS_TOPIC
TASK_RECORDED_CHANGE
CHANGE_AFFECTED_FILE
TASK_VERIFIED_BY
DEFINES
ANCHORS
```

Original Graph v0 rules:

- No import resolution.
- No task Markdown parsing.
- No symbol identity from name-only code index facts.
- No graph database storage.
- No llmwiki integration.
- MCP transport is a future layer over Graph build/query.

Current implementation has additive precise-provider capabilities beyond the original v0 baseline:

- Python AST symbol and anchor nodes.
- Python same-file, same-class, and imported cross-file `CALLS` edges when provider evidence is available.
- Python and JS/TS relative import resolution through `RESOLVES_TO` and `IMPORTS_FILE` edges when unambiguous.
- Graph completeness fields for identity collisions, metadata store validity, receipt completeness, parse errors, and provider failures.

These additions preserve the original rule that Graph is read-only and non-authoritative. They add facts and edges; they do not make `.repometa`, task Markdown, generated wiki, or Graph itself a source authority.

## Consequences

- Graph can be built before SCIP/LSP providers exist.
- Raw import references stay stable when resolution providers add `RESOLVES_TO` or `IMPORTS_FILE` evidence.
- Symbol names remain file facts until a provider can supply stable symbol identity and source anchors.
- Task evidence enters Graph only from task completion receipts.
- Repo-local topics avoid accidental cross-repo concept merging.
- Future providers must add facts or edges instead of changing v1 semantics.
