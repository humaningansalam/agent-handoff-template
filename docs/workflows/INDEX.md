# Workflow Index

Some workflow documents may also define lightweight operational standards when a separate standards document would add unnecessary structure. `repo-metadata.md` is one such workspace-level workflow document.

Use this index to decide whether a workflow applies before opening individual workflow files.

Do not read every workflow by default.
Read only the workflow(s) that clearly match the current task.

Use a workflow for a reusable, repeated, or high-risk procedure; keep one-off notes in the Task. Use stable semantic filenames and group by procedure type when useful.

## Active Workflows

### Quality / Review Workflows

For changes spanning agents, languages, or shared interfaces, read [Code Quality](code-quality.md).

For test changes, read [잘 작성된 테스트 코드](well-written-tests.md).

| Workflow | Use When | Do Not Use When | Tags | Expected Output |
| --- | --- | --- | --- | --- |
| `prd-backlog-sequential.md` | Comparing PRD/external notes to repo, listing missing work, or solving Backlog items one at a time | One-off read-only questions, immediate single known-file fixes | prd, backlog, discovery, sequential | PRD gap Backlog items or one archived Task with explicit Chosen scope per promoted item |
| `repo-metadata.md` | Creating/modifying graph-relevant files in `repos/`, or using `.repometa` to discover candidate files before task promotion | Root workspace files only, generated/vendor/build outputs | metadata, repo, graph, discovery | valid `repos/.repometa` policy/annotation state and inspected discovery candidates |
| `repoctl-upgrade.md` | Updating an adopting workspace to a newer repoctl/control-plane release | Product repo changes, project-specific workflow edits, or Board/Backlog/task cleanup | upgrade, release, control-plane | dry-run plan, inspected apply result, and workspace health checks |
