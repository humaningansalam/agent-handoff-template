# Agent Handoff Template

Starting documents for a project and work that continues across sessions.

## Use in a project

Adapt `AGENTS.md` to the project's actual commands and working practices. Put its problem, intended outcome and acceptance criteria in `docs/PRD.md`. Use `docs/REPOS.md` when several repositories need a shared map, and `docs/BOARD.md` when an overview of current or future work is useful.

A Task is an editable Markdown record for work that needs a handoff. Its body can contain a goal, findings, next action and result in the form that suits the work. The separate [repoctl](https://github.com/humaningansalam/repoctl) CLI creates and lists these records and changes their status. Finishing a Task leaves its document in place so existing links remain valid. Parent and dependency links describe relationships; each Task has its own status.

Copy only the documents the project needs, and replace their starter text with actual project information. The optional issue and pull request forms in `.github/` ask for the problem, desired result and relevant checks. Existing project contribution instructions and CI belong to that project.

## Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for changes to these starting documents.
