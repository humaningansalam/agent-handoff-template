# v0 Foundation Field Test

Use this before MCP, Graph, llmwiki, or multi-repo work. The goal is to verify the workspace control plane, not to test future layers.

## Must pass

- Create, start, and finish one docs task.
- Create, start, and finish one repo task.
- Promote one Backlog item with explicit fields.
- Block a Backlog-origin repo task without `## Discovery` evidence.
- Pass a Backlog-origin repo task with `## Discovery` evidence.
- Confirm dirty repo baseline separates pre-existing dirty files from task-new changes.
- Confirm repo HEAD changed after task start blocks finish.
- Confirm changed-file metadata gate blocks only changed-file issues.
- Confirm `move_candidate` gives an explicit `repoctl meta move` repair path.
- Confirm Korean `document_language` survives create/start/log/finish lifecycle.
- Confirm project-specific generated/cache paths are handled by `repo/.repometa/policy.json`, not core hardcode.
- Confirm `repoctl task doctor T-... --json` is read-only and reports recovery hints.
- Confirm completion audit distinguishes executable live work from explicitly blocked or deferred work that the user chose to keep.
- Confirm a monorepo-shaped `repo/` is treated as one product git repository with repo-relative paths, not as multiple workspace roots.

## Triage

- Structural bug: fix before the next layer.
- Pre-MCP/Graph/llmwiki/multi-repo blocker: queue before the next layer.
- Future-layer issue: do not patch v0 core.
- Intentional blocked/deferred work: do not hide it to make Board/Backlog look empty; record the user decision or ask before claiming completion.

## Do not do during this test

- Implement MCP, Graph, llmwiki, or multi-repo selectors.
- Add project-specific hardcode to core defaults.
- Add natural-language parsing of Backlog, PRD, or task prose.
- Repair adopter repo state just to make a test look clean.
