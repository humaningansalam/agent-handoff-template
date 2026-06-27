# Repository Map

This optional adopter-owned map explains product repository identities for humans. The authoritative source remains `repoctl repo list --json`.

| repo_id | Path | Role | Product Docs | Identity Source |
|---|---|---|---|---|
| `main` | `repos/` | Default product repository boundary for direct-layout workspaces | `repos/docs/**` or the product repo's own docs layout | reserved by repoctl direct layout |

## Ownership

- Root owns workspace control state, repoctl contracts, workflows, tasks, and Reviewed Knowledge records/events.
- Product repositories own product code and product documentation under their own `docs/**` trees.
- Do not infer repo identity from prose in this file; use repoctl registry output for automation.
