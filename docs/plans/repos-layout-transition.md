# repos/ layout transition plan

## Purpose

Move the product-code boundary from `repo/` to `repos/` and prepare repoctl for independent product repositories.

This plan is only about product repository identity, selected-repo routing, and repo metadata gates.

## Decisions

- The product-code boundary is `repos/**`; `repo/` compatibility is removed.
- Direct single-repo layout is `repos/.git` with reserved stable `repo_id: main`.
- Collection layout is `repos/<name>/.git`, but unconfigured children are candidates only, not operational repo targets.
- Stable multi-repo identity is pinned through `docs/repoctl.json` or `repoctl repo adopt`; basename suggestions are not stable IDs until adopted.
- Existing task model stays intact: parent/child tasks, `area`, Board, and archive behavior are not redesigned.
- `repo_id` is the product repository selector. Empty `repo_id` means no product repository is selected.
- `repo_ref` is advisory branch/worktree trace only; it must not select a repository.
- Product task start/finish gates apply to the selected product repo only. Other product repos being dirty is not a runtime blocker for the selected task.
- `repo check` validates global registry topology and identity; task/meta/index commands validate the selected repo.

## Layouts

Direct single product repo:

```text
repos/
  .git/
  .repometa/
  src/
```

Configured/adopted collection:

```text
repos/
  web/
    .git/
    .repometa/
  api/
    .git/
    .repometa/
```

Example config:

```json
{
  "document_language": "ko",
  "repositories": [
    { "id": "web", "path": "repos/web" },
    { "id": "api", "path": "repos/api" }
  ]
}
```

## Registry Model

```text
RepoTarget
- id
- root_path
- display_path
- identity_source: reserved | pinned

RepoCandidate
- path
- suggested_id
- git_toplevel
- validation_status
- identity_status: unbound

RepoLayout
- placement: empty | direct | collection
- registry_ready
- targets
- candidates
- revision
```

Rules:

- `repos/.git` produces `RepoTarget(id=main, path=repos, identity_source=reserved)`.
- Configured `repositories` produce pinned `RepoTarget`s.
- Unconfigured `repos/*/.git` produces `RepoCandidate`s only and `registry_ready=false`.
- `repo list` may show unbound candidates.
- `repo check` returns nonzero when candidates are unbound.
- Product mutation commands fail while `registry_ready=false`.
- `repos/.git` together with `repos/*/.git` is an ambiguity error.
- Config validation rejects duplicate IDs, casefold ID collisions, duplicate paths, absolute paths, `..`, workspace escapes, nested repo paths, and non-Git top-levels.
- Configured workspaces with extra unpinned `repos/*/.git` roots expose those roots as unbound candidates, set `registry_ready=false`, and block product mutation until adoption pins them.
- Git worktrees with `.git` files are valid product repo roots.

## Adoption

`repoctl repo adopt --all` pins all current unbound candidates into `docs/repoctl.json`.

`repoctl repo adopt repos/web --id web` pins one candidate.

Adoption requirements:

- Preserve existing `docs/repoctl.json` settings such as `document_language`.
- Validate the full new registry before atomic replace.
- `--all` is all-or-nothing.
- Single-candidate adoption merges into an existing registry; any remaining candidates keep `registry_ready=false`.
- Do not rebind an existing ID in v1.
- Do not move product files; layout migration is an operator action, not `repoctl upgrade` behavior.

## Task Integration

Task frontmatter keeps the existing shape and adds/uses `repo_id`:

```yaml
repo_id: ""
repo_ref: ""
area: ""
```

Semantics:

- Empty `repo_id`: no product repository selected.
- Non-empty `repo_id`: selected product repository for this task.
- Direct single layout may default repo-scoped task creation to `repo_id: main`.
- Collection layout requires explicit `repo_id` for repo-scoped task creation.
- Task start freezes selected repo identity: `repo_id`, `repo_path`, `git_toplevel`, HEAD, dirty baseline, and dirty fingerprints.
- Task finish verifies the selected repo target still matches the frozen target.
- Changed-file and `.repometa` gates run against the selected repo only.
- Cross-repo work uses existing parent task plus repo-specific child tasks.

## Command Integration

- `repoctl repo list/show/check/adopt` exposes registry diagnostics and adoption.
- Standalone `meta` and `index` commands use `--repo-id` when multiple pinned targets exist.
- Task lifecycle commands use task frontmatter and frozen state, not `repo_ref`, to select the product repo.
- JSON payloads include repository context where needed.

## JSON Contract

Repo-aware payloads keep file `path` repo-relative and add `workspace_path` when needed:

```json
{
  "repository": {
    "id": "web",
    "path": "repos/web",
    "identity_source": "pinned"
  },
  "files": [
    {
      "path": "src/app.ts",
      "workspace_path": "repos/web/src/app.ts"
    }
  ]
}
```

Problem codes include:

```text
repository_identity_unbound
repository_selector_required
repository_not_found
repository_topology_invalid
repository_registry_drift
repository_git_identity_drift
```

## Non-Goals

- No new `scope` enum.
- No coordination task replacement; existing parent tasks remain the coordination model.
- No Board/AGENTS snapshot or global mutation lease.
- No other-repo dirty or HEAD blocker for selected repo tasks.
- No automatic cross-repo task finish.
- No Graph relation inference or llmwiki authority over repo identity.
- No product file movement in `repoctl upgrade`.

## Tests

Required coverage:

- Direct `repos/.git` resolves to reserved `main`.
- Unconfigured `repos/web`, `repos/api` list as candidates only.
- `repo check` fails on unbound candidates.
- `repo adopt --all` creates pinned targets atomically.
- Configured multi-repo requires explicit `--repo-id` for repo-scoped task creation.
- Task start freezes selected `repo_id` and path.
- Task finish blocks selected repo target drift.
- Selected repo metadata gate runs with repo-relative `path` and workspace-relative `workspace_path`.
- Other repo pre-existing dirty state does not block selected repo start.
- Verification files are outside all registered product repos.
- Maintenance scope guards continue blocking `repos/**` as product code.
