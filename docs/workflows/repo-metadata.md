---
title: repo/ Metadata Operations (.repometa)
description: Maintain repo-local semantic metadata through JSON policy, hash-sharded sparse annotations, and changed-file checks.
tags:
  - metadata
  - repo
  - graph
expected_output:
  - valid repo/.repometa JSON policy and annotation shards
---

# repo/ Metadata Operations (.repometa)

This workflow defines the canonical file-level semantic metadata model for `repo/`.
Metadata is not stored inline in source files. The durable store lives in `repo/.repometa/`, and `repoctl` is the official mutation and validation boundary.

## Purpose

Provide graph-ready semantic metadata for important repo files without turning metadata into a full file inventory.
On-demand inventory and `repoctl index code` discover file nodes and technical facts. `.repometa` supplies policy defaults, coverage rules, sparse human/agent annotations, and explicit exclusion overrides.

## Authority

- `repo/.repometa/*` is the canonical semantic metadata store.
- Inline `@meta` blocks and metadata frontmatter inside `repo/` files are forbidden residue.
- Agents and humans should use `repoctl meta ...` commands instead of directly editing `.repometa` during normal work.
- Manual emergency edits are allowed only if followed by a clean `repoctl meta check`.

## Model

```text
repoctl inventory / repoctl index code
= all file nodes + technical facts

repo/.repometa/policy.json
= indexing excludes + vocab + area/topic defaults + coverage rules

repo/.repometa/annotations/<hex>.json
= sparse role/purpose/topics/declared_effects/caution + exclusion overrides
```

The store is not an inventory. General files can exist without annotations unless coverage policy requires one.

## Directory Layout

```text
repo/.repometa/
  policy.json
  annotations/
    0.json
    1.json
    2.json
    ...
    f.json
```

Annotation shard routing is deterministic:

```text
sha256(repo_relative_path)[0]
```

Use only 16 shards in v0. If scale requires more later, migrate deliberately; do not introduce semantic shard names.

## `policy.json`

```json
{
  "schema_version": 1,
  "indexing": {
    "exclude": [
      ".git/**",
      ".repometa/**",
      "**/*.png",
      "**/*.jpg",
      "**/*.jpeg",
      "**/*.gif",
      "**/*.webp",
      "**/*.zip",
      "**/*.tar",
      "**/*.gz"
    ]
  },
  "vocab": {
    "roles": {
      "base": ["service", "handler", "adapter", "component", "config", "test", "workflow", "migration", "spec", "script"],
      "extend": []
    },
    "declared_effects": {
      "base": ["none", "db", "net", "fs", "ui", "time", "crypto", "config"],
      "extend": ["cache", "queue", "email", "sms", "webhook", "push", "third_party"]
    }
  },
  "defaults": {
    "areas": {
      "backend": ["backend/**", "server/**", "api/**"],
      "frontend": ["frontend/**", "web/**"],
      "mobile": ["android/**", "ios/**", "lib/**"],
      "infra": [".github/**", "docker/**", "deploy/**", "**/Dockerfile"]
    },
    "topics": {
      "auth": ["**/auth/**", "**/*token*", "**/*login*"],
      "billing": ["**/billing/**", "**/pricing/**", "**/checkout/**"],
      "tests": ["**/tests/**", "**/*test*", "**/*.spec.*"]
    }
  },
  "coverage": {
    "require_annotations": [
      "frontend/src/api/**",
      ".github/workflows/**",
      "**/migrations/**",
      "**/specs/**",
      "**/adr/**"
    ]
  }
}
```

Keep the default policy ecosystem-neutral. Add project-specific dependency, cache, build, coverage, or generated-output paths to this repository's `repo/.repometa/policy.json` only when they apply to the adopted project.

Roles and declared effects are controlled but extendable through policy. Topics are open strings so agents do not need to rewrite policy for every local domain term.

## Annotation Shards

Example `repo/.repometa/annotations/a.json`:

```json
{
  "schema_version": 1,
  "annotations": {
    "frontend/src/api/authClient.ts": {
      "role": "adapter",
      "purpose": "call backend authentication endpoints",
      "topics": ["auth", "api"],
      "declared_effects": ["net"],
      "caution": ["keep login response compatibility"]
    }
  },
  "exclusions": {
    "frontend/src/api/service_stub.ts": {
      "reason": "test_stub",
      "excluded_by": "agent"
    }
  }
}
```

Required annotation fields:

- `role`: structural role of the file
- `purpose`: one concise English sentence
- `topics`: repo-local subject tags

Recommended:

- `declared_effects`: semantic risk hints such as `db`, `net`, `ui`, `crypto`, `time`, `fs`, `config`, or `none`

Optional:

- `caution`: human/agent warning not derivable from code

Forbidden fields:

`id`, `path`, `language`, `kind`, `imports`, `calls`, `deps`, `symbols`, `observed_effects`, `relates_to`, `last_reviewed`, `version`

Technical facts such as language, imports, calls, deps, symbols, and observed effects belong to `repoctl index code` or a future deeper code indexer, not `.repometa`.

## Classification

`repoctl meta inventory --json` classifies files as:

```text
excluded
indexed_only
annotation_required
annotated
excluded_override
orphan_annotation
orphan_exclusion
move_candidate
```

Priority order:

```text
1. hard exclude from policy.json indexing.exclude
2. explicit exclusion override
3. existing annotation
4. coverage.require_annotations
5. indexed_only
```

Coverage does not override hard excludes.

## repoctl Commands

Read/status commands:

```bash
repoctl meta init --json
repoctl meta inventory --json
repoctl meta status --changed --json
repoctl meta query --topic <topic> --role <role> --area <area> --json
repoctl meta suggest --text "short feature or PRD phrase" --json
repoctl meta check --changed --json
repoctl meta check --json
repoctl meta show <path> --json
```

Discovery commands are read-only helpers for task planning:

- `repoctl meta query` returns files matching explicit metadata/default filters such as role, topic, area, or declared effect.
- `repoctl meta suggest` returns non-authoritative candidate files from path, filename, policy defaults, and sparse annotation text. It preserves Unicode query tokens, but it is lexical search, not translation or semantic parsing.
- Suggestions must be inspected before task creation or implementation. They do not define task scope, do not parse Backlog raw text, and do not replace repo/code reading.

Use `repoctl meta init` to create the default `repo/.repometa` policy and shard skeleton for a new `repo/`. It does not overwrite an existing policy or shard.

Mutation commands update `.repometa` through repoctl:

```bash
repoctl meta set <path> --role ... --purpose "..." --topic ... --declared-effect ...
repoctl meta set <path> --role ... --purpose-file /tmp/purpose.txt --topic ...
repoctl meta remove <path>
repoctl meta move <old-path> <new-path>
repoctl meta exclude <path> --reason ...
```

Do not add migration or inline compatibility commands in v0.

## Check Semantics

`repoctl meta check` validates overall metadata health:

- JSON syntax and schema for `policy.json` and annotation shards
- shard names and path hash routing
- duplicate paths across shards
- annotation paths and exclusion paths exist
- annotation schema and controlled vocab values
- forbidden fields are absent
- coverage-required files have annotations
- inline metadata residue is reported

`repoctl meta check --changed` is the task-finish gate:

- uses the changed set from the nested `repo/` git repository
- includes tracked, staged, renamed, copied, deleted, and untracked files
- blocks the current task on changed-file metadata errors
- reports rename metadata as `move_candidate`; agents must confirm and run `repoctl meta move`
- does not block on unrelated full-repo metadata health

When there are no `repo/` changes and `repo/` is a valid independent git repository, `repoctl task finish` skips the gate with `reason = no_repo_changes`.

If `repo/` exists but its git metadata is missing or unusable, changed-file metadata status/check and task finish must fail with `repo_git_unavailable`. Treat this as a blocked verification gate, not as `no_repo_changes`.

## Discovery Before Task Promotion

Use `.repometa` discovery before promoting PRD-derived Backlog items or starting unfamiliar repo work.

Recommended sequence:

```bash
repoctl meta query --topic <known-topic> --json
repoctl meta suggest --text "<PRD phrase or feature name>" --json
repoctl meta show <candidate-path> --json
```

Then inspect the candidate files directly in `repo/` before creating or starting a task.

Discovery output is evidence, not authority:

- Good: record which candidates were reviewed and why the task scope was chosen.
- Required: for Backlog-origin tasks that change `repo/`, fill the task's `## Discovery` section before `repoctl task finish`.
- Bad: create a task or choose files solely because `meta suggest` returned them.
- Forbidden: parse Backlog or PRD prose inside repoctl to infer area, files, validation, or task metadata.

## Path Normalization

Before hashing or storing keys, paths are normalized to repo-relative forward-slash form:

```text
./frontend/src/api.ts
repo/frontend/src/api.ts
frontend\src\api.ts
```

all route to:

```text
frontend/src/api.ts
```

Paths with `..` are invalid.

## Agent Etiquette

| Event | Action |
| --- | --- |
| Creating a covered file | Add or update annotation through repoctl. |
| Modifying a covered file | Re-evaluate purpose/topics/declared_effects/caution. |
| Deleting a file | Remove orphan annotation through repoctl. |
| Moving a file | Move annotation only after confirming it is the same logical file. |
| Seeing inline @meta | Treat it as forbidden residue and remove it after adding `.repometa` annotation if needed. |
| Seeing a coverage false positive | Use `repoctl meta exclude <path> --reason ...`, not a filename-specific code exception. |

## Move Recovery

`repoctl meta move` writes the new shard before removing the old shard so interrupted moves prefer duplicate/stale metadata over data loss.

If a move is interrupted:

```bash
repoctl meta check --json
```

Then repair explicitly:

```bash
# Old file was renamed and the new annotation is correct.
repoctl meta remove <old-path>

# New path was written by mistake.
repoctl meta remove <new-path>
```

Run `repoctl meta check --json` again after repair. Do not hand-edit shards unless repoctl cannot read the JSON at all.

## Graph Use

Graph construction combines:

1. file/code index: all file nodes and technical facts
2. `.repometa` policy: area/topic defaults and coverage rules
3. `.repometa` annotations: role, purpose, topics, declared_effects, caution

This preserves backend/frontend/topic/effect queries without requiring inline comments or all-files manifest entries.
