# repoctl module boundaries

These boundaries keep repoctl stable before MCP, Graph, llmwiki, and broader repo-layout layers exist.

## Ownership

- `task lifecycle` owns task frontmatter, Board membership, archive transitions, start baselines, finish gates, Handoff, Execution Log, and Verification updates.
- `backlog` owns opaque raw block CRUD only.
- `meta` owns `.repometa` policy, annotations, exclusions, move repair, and metadata validation.
- `index` owns read-only technical facts such as language, imports, symbols, calls, dependencies, and observed effect hints.
- `cli` owns argparse, JSON envelopes, human presentation, and command wiring only.

## Future layer rules

- Future MCP must call stable repoctl command handlers or consume the JSON contract; it must not parse human stdout.
- Future Graph must derive relation data from index facts, task evidence, and `.repometa`; it must not store graph fields inside `.repometa` annotations.
- Future Graph/Index owns observed repo-specific topics; `.repometa` topics are human hints and bootstrap labels, not the authoritative topic graph.
- Future llmwiki must promote stable knowledge from task/archive evidence; it must not replace task frontmatter, Board, Backlog, or `.repometa` authority.
- Monorepo layouts should treat packages/apps/services inside the selected product repo as scoped surfaces, not as separate workspace roots.
- Multi-repo support must use explicit repo selectors/namespaces; MCP, Graph, and llmwiki must not infer repo identity from path strings alone.

## Repo layout direction

The preferred single product git repository lives at `repos/`. That repository may be a monorepo internally, with apps, packages, services, infra, and docs under repo-relative paths.

Configured multi-repo workspaces use stable repo IDs in task metadata, changed-file gates, JSON payloads, and future Graph/MCP schemas. Do not add ad hoc `repo2/`, `api/`, or `web/` mutation paths outside the registry.

## Forbidden shortcuts

- No natural-language parsing of Backlog, PRD, or task prose into scope/area/files.
- No project-specific hardcode in core defaults.
- No direct `.repometa` mutation outside repoctl commands in normal operation.
- No state-changing recovery hidden inside diagnostics such as `task doctor` or `next_actions`.
