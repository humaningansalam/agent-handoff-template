# Split PRD Pattern (Optional Example)

This folder shows an optional split-PRD structure for projects that outgrow a single
`project/PRD.md`.

Use this pattern only when:
- the PRD becomes too large for efficient agent reads
- different tasks repeatedly need different slices of product context
- a single PRD file starts causing context-window bloat

Suggested migration path:
1. If a project adopts split PRD mode, turn `project/PRD.md` into a thin index / reading guide.
2. Create `project/prd/` in the real project
3. Copy and adapt the example files in this folder
4. In each task, list only the needed PRD files under `## Context Docs`

This is a reference-only example.
Do not treat files in `examples/prd/` as active project context.
