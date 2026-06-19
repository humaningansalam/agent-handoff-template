# .repometa identity policy v0

## Status

Accepted for v0 foundation.

## Decision

- v0 file identity is the normalized repo-relative path.
- v0 repo identity is explicit when configured and otherwise reserved as `main` for a single product repository at `repos/`.
- `repoctl meta move` is the only identity-continuity operation.
- Content hash and symbol hash are future Graph signals, not v0 identity.
- `.repometa` must not store graph relation fields.
- Path drift is handled by explicit repair in v0, not automatic inference.

## Rationale

`.repometa` is a sparse semantic metadata store and changed-file gate input, not a graph database. Automatic rename or relation inference in v0 would create false continuity and make later Graph construction harder to trust.

Path identity is simple, reviewable, and matches the current task gate. When files move, an agent or human must inspect the change and run `repoctl meta move <old-path> <new-path>`.

## Non-goals

- No automatic rename inference.
- No content-addressed identity in v0.
- No automatic multi-repo namespace inference in v0.
- No `imports`, `symbols`, `calls`, `deps`, `relates_to`, or graph edge fields in annotations.
- No llmwiki authority over `.repometa` identity.

## Topic boundary

v0 `.repometa` topics are sparse human hints and policy bootstrap labels, not authoritative repo understanding. Future Graph/Index work should derive observed repo-specific topics from code structure, routes, imports, tests, configuration, and co-change signals; manual topics may remain as reviewed semantic hints and stale-topic warnings should surface conflicts.

## Future compatibility

Graph may later use path, content hash, symbol hash, and code index facts as signals. Those signals can suggest repair, but repoctl v0 identity remains explicit until a later ADR changes it.

Monorepo layouts inside the selected product repo remain compatible with v0 because file identity is still repo-relative, for example `apps/web/src/page.tsx` or `packages/api/src/server.ts`.

Multi-repo workspaces must use the repo registry namespace before Graph, MCP, or llmwiki rely on file IDs. A future file node might become `repo:<repo_id>:file:<path>` or an equivalent stable shape, but v0 must not guess that namespace from prose or arbitrary path strings.
