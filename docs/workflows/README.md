# Workflows

This folder contains project-level reusable procedures and, when appropriate, lightweight operational standards tightly coupled to how work is performed.

Use it for repeated, risky, or order-sensitive project procedures shared across humans and agent tools. Do not put tool-specific executable behavior or platform automation here; those belong in `.agents/skills/`.

Start with `INDEX.md` to decide whether a workflow applies.
Use `TEMPLATE.md` when creating a new workflow.

## Product execution handoff

For each product, one `gpt-6-astra` / `medium` Worker owns implementation, debugging and verification serially. The `Terra` / `medium` Coordinator relays work and consumes returns; Expert Director specifies technical work and reviews the implementation return; Main handles product judgment. Keep the existing Main/Expert provider and model selections. Common-tool engineering remains direct `gpt-6-astra` / `max` work.

Before switching implementation ownership, recover live Expert/Worker requests, command handles and unconsumed results. Keep a running command with its current owner until its result or deliberate handoff is recovered. Reuse the existing suitable Worker, applying its assigned model/effort through supported runtime settings and confirming the actual setting receipt and next turn. For an idle/completed Worker, use its supported follow-up/start/resume call and confirm a new running turn or recover its immediate terminal result; message acceptance or old Interacted output alone does not establish execution.

Use the existing Task/Handoff to carry the current WP/AC, exact HostSpan target/session and workspace, source/diff and original execution/result references, effective constraints and next action. A supplier's read-only lookup limit stays scoped to that lookup; it does not pause the consumer's already authorized next phase. Update the current handoff instead of copying historical instructions into another request.

At existing review points, Worker returns changes and verification results to Expert; the same Worker applies corrections and continues through the authorized outcome. Coordinator consumes terminal results, including failures, and connects the next authorized WP/AC without restarting Main/Expert review for each small edit. Keep one supported native Worker wait or completion callback through result consumption; a Completed badge or callback registration alone does not establish parent consumption. Use [External completion](#external-completion) for external returns.

Execution uses exact current target/operation inspection, existing scoped authorization and normal fresh guards. Full argv projection is not a universal approval gate. Preserve actual external review refusals and active review, authentication and resource holds with their original scope and reason.

## External completion

Keep waiting in the tool that owns the original work: SessionPlane collects its request/continuation chain, HostSpan waits on its process handle, and CI watches its existing run. Native agent waits observe actual native agent/thread IDs; an empty agent wait is not a wait for a provider request, remote process, or CI run. Repoctl resumes Task/Handoff context, not external execution.

When launching a long external wait, save its result and deliver the native queue message in the original waiter's outcome branch, inside the same externally running command. This runs without another model turn collecting the handle. Preserve the live collector rather than starting a competing watch; keep its execution handle, destination thread and result reference in the existing Handoff. Send compact execution/result references and actual status or human action needed. Shared-resource messages go once to the affected existing peer only when its next actual use changes: current START/conflict or normal END, last access and cleanup limits. Keep unrelated verification and historical supplements in the original Task/Handoff. Unchanged waits and already consumed results need no new message or model turn.

```sh
# Part of the original external wait command, after saving its actual outcome.
# Use the verified owning-host endpoint and original Coordinator thread.
codex queue --remote "$codex_remote" \
  --thread "$coordinator_thread_id" --message "$completion_refs"
```

The owning host supplies `codex_remote` (for example, its existing `unix:///…/app-server-control.sock`), `coordinator_thread_id` and `completion_refs`. Queue delivery waits for a busy thread's normal idle boundary and respects an interrupted thread. Before retrying uncertain delivery, reconcile the existing queue and receiving records; do not force-start the recipient. Queue ID proves acceptance; consumption is the receiving turn retrieving and using the original result. A delayed notice for the same use is historical after a later END/result was consumed; it does not reopen finished use or need another ACK, Root readiness approval or freshness round. Repoctl debug records context recovery, not delivery. Preserve actual conflicts, live executions, cancellations, actionable errors and human-needed states.
