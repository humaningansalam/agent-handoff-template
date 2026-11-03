# Architecture Overview

`docs/` holds product direction, capability requirements/design, decisions, research, workflows, and current Task records. `repos/` contains independent product Git roots. repoctl reads both while preserving repository identity and dirty baselines.

Task/Board/Workflows form the operating layer. Handoff is a Task section. Graph and Knowledge form the derived understanding/reuse layer. Graph snapshots, provider artifacts, evidence indexes, and Knowledge projections are rebuildable; their failure must not replace or hide valid source records.

The Graph common model is node/edge/source with confirmed relations distinct from candidates. Source overlays exclude stale stored relations. Knowledge authority is the direct v2 record; the projection is an optimization. Graph source search reuses bounded internal evidence ranking without registering another user-facing source or export layer.

External agent execution, model/provider assignment, queues, deployment, and future MCP access remain outside this repository's ownership boundary.

Graph evidence index schema 9 stores source-declared document authority, preserving explicit draft/proposed status across SQLite retrieval without introducing an approval database. An existing older derived index is disposable and must be rebuilt.
