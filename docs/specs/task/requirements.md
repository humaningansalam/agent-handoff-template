# Task Requirements

A Task contains current work only: Goal, actual Chosen scope, current decisions/constraints, meaningful result or blocker, next action/done condition, and source-document references. Bulk output, dirty/residue arrays, fixtures, copied inventories, role/request/ACK state, and repeated history do not belong in it.

Lifecycle commands keep full machine detail in structured command output or receipts while Markdown records counts and a stable detail reference. Chosen replacement logs record reason and added/removed counts; Discovery contains the current list once.

Handoff has exactly four restart fields. Freshness observes the Task contract, Discovery scope/Notes, repository observation, child lifecycle, and Handoff. Execution Log and optional Verification prose are not freshness inputs. A changed next action must be represented in the current contract, Notes/constraints, or Handoff.

`task audit` is read-only and warning-only. `task migrate-slop` is dry-run by default and applies only recognized generated-line rewrites to live Tasks. Immutable archives are reported but not rewritten without receipt-aware migration.
