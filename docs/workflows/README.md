# Workflows

This folder contains project-level reusable procedures and, when appropriate, lightweight operational standards tightly coupled to how work is performed.

Use it for repeated, risky, or order-sensitive project procedures shared across humans and agent tools. Do not put tool-specific executable behavior or platform automation here; those belong in `.agents/skills/`.

Start with `INDEX.md` to decide whether a workflow applies.
Use `TEMPLATE.md` when creating a new workflow.

## External completion

Keep waiting in the tool that owns the original work: SessionPlane collects its request/continuation chain, HostSpan waits on its process handle, and CI watches its existing run. Native agent waits observe actual native agent/thread IDs; an empty agent wait is not a wait for a provider request, remote process, or CI run. Repoctl resumes Task/Handoff context, not external execution.

For a long external wait, use the original owner's existing waiter and terminal branch. After collecting the outcome, that same process can notify the original Coordinator through Codex's native queue. Keep the execution handle, destination thread and result reference in the existing Handoff when handing over collection; preserve a live collector rather than starting a competing watch. Send compact task/execution IDs, terminal status and result/error references, or the concrete human action needed. Progress output and unchanged waits need no new model turn.

```sh
# In the existing waiter's outcome branch, after saving its actual result.
# Use the verified owning-host endpoint and original Coordinator thread.
codex queue --remote "$codex_remote" \
  --thread "$coordinator_thread_id" --message "$completion_refs"
```

The owning host supplies `codex_remote` (for example, its existing `unix:///…/app-server-control.sock`), `coordinator_thread_id` and `completion_refs`. Queue delivery waits for a busy thread's normal idle boundary and respects an interrupted thread. Reconcile uncertain delivery against the existing queue and receiving messages before retrying the same execution/result; do not force-start the recipient. Queue acceptance is not result consumption: the receiving turn retrieves the original outcome and acknowledges its references. A canceled execution, actionable error or human-needed state retains that meaning rather than becoming a success notification.
