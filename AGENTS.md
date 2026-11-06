# Template-only Agent Guide

This workspace uses the human document contract in `docs/template-contract.json`. No repoctl, index, graph, agent runner, or automation installation is required.

1. Read `docs/PRD.md`, `docs/BOARD.md`, and only the relevant project spec or contract sources.
2. Create one live Task from `docs/tasks/TEMPLATE.md`, give it a unique `T-YYYYMMDDHHMMSSZ` ID, and register its exact path in `docs/BOARD.md`.
3. Keep Goal, Context Docs, current Chosen file scope, Notes, meaningful Execution Log and the four current Handoff fields human-authored. The four labels are **Next exact step**, **First file to open**, **First command to run**, and **Done when**.
4. Check the selected repository's actual source and local instructions before acting. A search result never becomes permission, selected scope, verification evidence or approval.
5. Record actual test results and blockers honestly. Do not infer verification from a Task status or a generated Handoff. Move completed work out of the live Board and preserve its history.

The document forms work manually. A compatible repoctl installation may validate or operate on them, but its presence is optional; do not write its generated state by hand.
