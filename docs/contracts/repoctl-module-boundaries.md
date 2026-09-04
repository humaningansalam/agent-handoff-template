# repoctl module boundaries

These boundaries keep repoctl stable across task lifecycle, repository metadata, Graph, Context / Task Pack, Reviewed Knowledge, llmwiki rendering, and deferred transports.

## Ownership

- `task lifecycle` owns task frontmatter, Board membership, archive transitions, start baselines, finish gates, Handoff, Execution Log, Verification updates, the compact machine-owned Handoff binding receipt, and workspace live-task resume selection.
- `task lifecycle` also owns exact completion-receipt artifact resolution across live and archived task locations. Knowledge, Graph, Context, and llmwiki consume that typed identity result instead of guessing archive paths or rewriting immutable provenance.
- `Context / Task Pack` owns canonical Pack input collection, source-identity projection, artifact rendering, and read-only Pack integrity/freshness inspection. It does not write Handoff or task lifecycle state.
- `context_sources` owns the typed `current_source | config | structured_data` classification used by both live fallback and the persistent evidence index. Retrieval and compact projection consume that role; they must not reclassify paths with folder keywords or error-text heuristics.
- `result receipts` own the canonical producer request, repository/result identity, bounded citable membership independent of display visibility, and the atomic idempotent regenerable cache. Discovery consumes this typed contract instead of reconstructing query provenance.
- `history projection` owns the content-bound completion catalogue, incremental checkpoint, versioned file-outcome frontier, Knowledge current-head index, and exact cold-history lookup shared by Graph, Context, Knowledge, and llmwiki. No consumer independently walks the archive during ordinary query, freshness, or incremental update.
- The outcome contract owns canonical member capsules, explicit episode dispositions, completion-bound verification, and explicit history inspection; producer result receipts remain flat selections that Discovery validates before freezing a capsule.
- `backlog` owns opaque raw block CRUD only.
- `meta` owns `.repometa` policy, annotations, exclusions, move repair, and metadata validation.
- `index` owns read-only technical facts such as language, imports, symbols, calls, syntax-resolved structured file dependencies, and observed effect hints.
- `cli` owns argparse, JSON envelopes, human presentation, and command wiring only.
- `debug diagnostics` owns opt-in, sanitized, bounded observations of repoctl invocations under ignored task state. It does not own task or product state.

## Derived layer rules

- MCP, if added, must call stable repoctl command handlers or consume the JSON contract; it must not parse human stdout.
- Debug events may describe direct commands and Context-mediated evidence, but never create authority, scope, Handoff freshness, Verification, or a claim that exposed evidence affected a decision.
- Graph derives relation data from index facts, task evidence, and `.repometa`; it must not store graph fields inside `.repometa` annotations.
- The component-crossing surface is an additive Graph projection over provider-recognized manifest identity and existing typed relations. It is not `.repometa` ownership, directory-derived authority, or a second graph.
- CLI may compose Task Handoff and optional Pack observations into resume guidance, but it must not infer prose meaning, auto-bind on start/show, regenerate stale artifacts, or duplicate the input projection owned by Context / Task Pack.
- The Dart semantic provider owns resolved RPC source facts, actual-to-formal invocation validity, routine/parameter bindings, schema-selection evidence, and source coverage. The structured resolver owns SQL compatibility and the linked/non-linked outcome. Graph materializes linked edges and separately projects compatible non-linked candidates; no second Dart scanner or fallback owns the same decision.
- Graph/Index owns observed repo-specific topics; `.repometa` topics are human hints and bootstrap labels, not the authoritative topic graph.
- Cold receipts and Knowledge events are immutable audit evidence; the active derived layer contains current repository subjects, fixed-size versioned file-outcome cells, bounded retained completion events, and current Reviewed Knowledge heads. Graph `--task` / `--artifact` and Context `past_decision` / `failure_mode` are explicit cold-history consumers that return bounded ephemeral results without copying those records into ordinary Graph or Context indexes.
- Context cold matches appear only in `related_history` and are logically isolated from current candidates, ranking, Graph seeds, traversal, component projection, and scope inputs. llmwiki renders the active current projection by default; historical audit access remains an explicit bounded boundary rather than part of the default search index.
- Receipt-derived Knowledge keeps its declared source refs and record digest unchanged. Public navigation may expose a separately resolved source ref only when the receipt, task/repository identity, declared artifact path, unique current artifact, and content digest all agree; a verified `docs/tasks/**` to `docs/archive/tasks/**` move is current rather than stale.
- Monorepo layouts should treat packages/apps/services inside the selected product repo as scoped surfaces, not as separate workspace roots.
- Multi-repo support must use explicit repo selectors/namespaces; MCP, Graph, and llmwiki must not infer repo identity from path strings alone.

## Repo layout direction

The preferred single product git repository lives at `repos/`. That repository may be a monorepo internally, with apps, packages, services, infra, and docs under repo-relative paths.

Configured multi-repo workspaces use stable repo IDs in task metadata, changed-file gates, JSON payloads, Graph, Context, Knowledge, and any future MCP transport. Do not add ad hoc `repo2/`, `api/`, or `web` mutation paths outside the registry.

## Forbidden shortcuts

- No natural-language parsing of Backlog, PRD, or task prose into scope/area/files.
- No project-specific hardcode in core defaults.
- No direct `.repometa` mutation outside repoctl commands in normal operation.
- No state-changing recovery hidden inside diagnostics such as `task doctor` or `next_actions`.
