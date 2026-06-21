# ADR: repoctl Graph v0

## Status

Accepted

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

Graph v0 rules:

- No import resolution.
- No task Markdown parsing.
- No symbol identity from name-only code index facts.
- No graph database storage.
- No llmwiki integration.
- MCP transport is a future layer over Graph build/query.

## Consequences

- Graph can be built before SCIP/LSP providers exist.
- Raw import references stay stable when future resolution providers are added.
- Symbol names remain file facts until a provider can supply stable symbol identity and source anchors.
- Task evidence enters Graph only from task completion receipts.
- Repo-local topics avoid accidental cross-repo concept merging.
- Future providers must add facts or edges instead of changing v1 semantics.
