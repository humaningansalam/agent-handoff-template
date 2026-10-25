# Workflows

This folder contains project-level reusable procedures and, when appropriate, lightweight operational standards tightly coupled to how work is performed.

Use it for repeated, risky, or order-sensitive project procedures shared across humans and agent tools. Do not put tool-specific executable behavior or platform automation here; those belong in `.agents/skills/`.

Start with `INDEX.md` to decide whether a workflow applies.
Use `TEMPLATE.md` when creating a new workflow.

## External completion

Keep waiting in the tool that owns the original work: SessionPlane collects its request/continuation chain, HostSpan waits on its process handle, and CI watches its existing run. Native agent waits observe actual native agent/thread IDs; an empty agent wait is not a wait for a provider request, remote process, or CI run. Repoctl resumes Task/Handoff context, not external execution.

When launching a long external wait, include result saving and native queue delivery in the original waiter's outcome branch, within the same externally running command. Delivery then runs without a later model turn collecting the handle. Keep the execution handle, destination thread and result reference in the existing Handoff when handing over collection; preserve a live collector rather than starting a competing watch. Send compact task/execution IDs, terminal status and result/error references, or the concrete human action needed. Progress output and unchanged waits need no new model turn.

```sh
# Part of the original external wait command, after saving its actual outcome.
# Use the verified owning-host endpoint and original Coordinator thread.
codex queue --remote "$codex_remote" \
  --thread "$coordinator_thread_id" --message "$completion_refs"
```

The owning host supplies `codex_remote` (for example, its existing `unix:///…/app-server-control.sock`), `coordinator_thread_id` and `completion_refs`. Queue delivery waits for a busy thread's normal idle boundary and respects an interrupted thread. Reconcile uncertain delivery against the existing queue and receiving messages before retrying the same execution/result; do not force-start the recipient. The queue ID in the original command output records acceptance; the receiving turn's retrieval and acknowledgement of the original result records consumption. Repoctl debug records context recovery, not these delivery events. A canceled execution, actionable error or human-needed state retains that meaning rather than becoming a success notification.
