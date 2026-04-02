---
title: DB Migration
use_when:
  - schema or data migration is ordered, risky, or repeatedly needed
do_not_use_when:
  - the change is a one-off local experiment
tags:
  - db
  - migration
  - schema
expected_output:
  - migration notes recorded in the active task file
  - verification evidence captured
---

# DB Migration Workflow (Example)

Reference-only example for database schema changes.

## Purpose

Use this workflow when database schema or data migrations are high-risk, order-sensitive,
or repeated often enough to benefit from a reusable SOP.

## Use When

- schema changes must be applied in a specific order
- rollback planning is important
- multiple services or queries may be affected
- the same migration procedure is repeated across tasks

## Do Not Use When

- the change is a one-off local experiment
- the task can be fully explained inside a single task file
- no reusable migration procedure is needed

## Preconditions

- migration scope is defined
- affected services or queries are identified
- rollback approach is known before execution
- active task file exists and is being kept up to date

## Steps

1. Document migration scope and affected services in the active task file.
2. Define rollback approach before applying changes.
3. Apply the migration in a controlled order.
4. Verify schema shape and application behavior.
5. Record evidence and final status in the active task file.

## Verification

- run the migration in a non-production environment first
- validate the expected schema shape
- run targeted tests around changed tables and queries
- verify key application read/write paths
- confirm rollback plan remains usable if work pauses

## Notes

In `project/tasks/T-YYYYMMDDHHMMSSZ--slug.md`, record:

- migration command(s) executed
- verification command(s) and outcomes
- rollback status and final result
- next safe step if work is paused