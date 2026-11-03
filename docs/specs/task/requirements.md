# Task Requirements

A Task contains current work only: Goal, actual Chosen scope, current decisions/constraints, meaningful result or blocker, next action/done condition, and source-document references. Bulk output, dirty/residue arrays, fixtures, copied inventories, role/request/ACK state, and repeated history do not belong in it.

Lifecycle commands keep full machine detail in the immediate structured command output while Markdown records bounded counts. No fake persistent detail reference is invented. Chosen replacement logs record reason and added/removed counts; Discovery contains the current list once.

Handoff has exactly four restart fields. Freshness observes the Task contract, Discovery scope/Notes, repository observation, child lifecycle, and Handoff. Execution Log and optional Verification prose are not freshness inputs. A changed next action must be represented in the current contract, Notes/constraints, or Handoff.

`task audit` is read-only and warning-only. `task migrate-slop` is dry-run by default and applies only recognized generated-line rewrites to live Tasks. Default apply never changes closed `done`/`canceled` Tasks, even if a child still lives under `docs/tasks/`. A single explicitly selected closed Task can be cleaned with `--closed`, after checking the original completion receipt and saving an original-byte migration witness without rewriting history as new verification. Atomic source writes and validated rollback preserve the original on failure.

A current v5 completion receipt may explicitly mark `historical_transition_evidence.status=not_recorded` only when the one-time converter preserved a byte-identical original receipt and a verifiable conversion witness. This represents an old completion without originally recorded start/transition events; it cannot supply descendant path lineage or count as newly verified historical timing. The original Task content hash and actual recorded results remain unchanged. Pre-existing Task drift and genuinely inconsistent partial evidence are separate blockers.

An exact old receipt cited by Knowledge requires finalizing any active v1 first. The optional one-ID `task migrate-receipts --relink-knowledge` transition relocates SHA-verified current v2 source citations to the byte-identical archived original, preserving authored claims, reasons and timestamps; it rejects mismatched digests, dependent citations and concurrent changes. It does not add a legacy reader to normal execution.
