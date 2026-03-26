# DB Migration Workflow (Example)

Reference-only example for database schema changes.

## Purpose

Use this as a reusable SOP when schema changes are high-risk or repeatedly needed.

## Safety-First Rules

- Prefer additive, backward-compatible changes.
- Separate schema migration from destructive data cleanup.
- Never run destructive changes without explicit approval and rollback plan.

## Change Checklist

1. Document migration scope and affected services.
2. Define rollback approach before applying changes.
3. Apply migration in controlled order.
4. Verify schema and application behavior.
5. Capture evidence in the active task file.

## Verification Steps

- Run migration in non-production environment first.
- Validate expected schema shape.
- Run targeted tests around changed tables/queries.
- Verify application startup and key read/write paths.

## Task File Record Requirements

In `docs/tasks/T-YYYYMMDDHHMMSSZ--slug.md`, record:

- migration command(s) executed
- verification command(s) and outcomes
- rollback status and final result
- next safe step if work is paused
