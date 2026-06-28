# Repository Map

This optional adopter-owned map explains product repository identities for humans. The authoritative source remains `repoctl repo list --json`.

| repo_id | Path | Role | Identity Source |
|---|---|---|---|
| `main` | `repos/` | Default product repository boundary for direct-layout workspaces | reserved by repoctl direct layout |

## Ownership

- Root owns private workspace control state, tasks, workflows, contracts, PRD/context, and repoctl tooling.
- Product repositories own product code and product-repo-local documentation. Product docs inside a public product repo should be public-safe.
- Do not infer repo identity from prose in this file; use repoctl registry output for automation.
