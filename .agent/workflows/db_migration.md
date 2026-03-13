# ⚙️ Workflow: Database Migration

> **Agent Note:** Strictly follow these steps when making any changes to the database schema.

## 1. Safety First
- **NEVER** drop a table or delete columns without explicit human approval.
- Always assume there is production data that needs to be preserved.

## 2. Step-by-Step Execution
1. Create a backup script for the target tables.
2. Write the migration SQL / Prisma schema update.
3. Run local tests to verify the schema change doesn't break existing queries.
4. Update `SESSION_HANDOFF.md` detailing exactly what schema was changed.

## 3. Verification
- Run `npm run lint` and `npm run test:db` after changes.
