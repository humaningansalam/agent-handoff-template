# Product Requirements: Agent Workspace Control Plane

## Problem

Coding agents lose important state across sessions and tools. Chat history is incomplete, repository boundaries are easy to cross by accident, unstructured task prose drifts from actual files, and broad rediscovery wastes time in large repositories.

The workspace needs a small durable model that helps an agent answer four questions:

1. What am I working on now?
2. What exact step should I take after a pause?
3. What source material should I read?
4. Which past conclusion and reason are worth reusing?

It must answer those questions without turning normal development into evidence registration or approval administration.

## Product model

- **Task** is the current unit of work. Its frontmatter owns lifecycle state and its Chosen set owns intended product file scope.
- **Handoff** is an explicitly reviewed four-field restart instruction.
- **Context** is a bounded, source-linked read-only view over current code, project documents, Graph relations, task history, and Knowledge.
- **Knowledge** is an optional reusable conclusion plus its reason and sources.

Supporting mechanisms stay narrow:

- product repositories have explicit identities under `repos/`
- task start records a repository baseline and protects pre-existing dirty work
- `.repometa` supplies sparse human file metadata and changed-file gates
- Graph and the persistent evidence index accelerate navigation without becoming authority
- completion history preserves finished Task and repository-change identity

## Users

- An agent resuming work without reliable chat history.
- A human coordinating work across coding tools.
- A team keeping private workspace state separate from product repositories.
- An agent locating owners, tests, callers, contracts, or past decisions in a large repository.

## Primary flows

### Small known change

1. Create and start a product Task.
2. Record the known file or coherent file set as Chosen.
3. Edit and run the checks useful for the change.
4. Optionally keep concise results in `## Verification`.
5. Finish the Task.

No Context, Graph, Pack, verification registration, or Knowledge action is required.

### Ambiguous change

1. Query Context, inspect Git, use Graph, or search directly before mutation.
2. Read the strongest source candidates and refine only when needed.
3. Create and start a Task.
4. Record Chosen and a Note only for a decision worth remembering.
5. Implement and finish through the same normal flow.

Search queries, opened files, ranked candidates, and result selections are not durable Task state.

### Pause and resume

1. Write the next step, first file, first command, and done condition.
2. Bind that exact Handoff before a real pause or transfer.
3. On the next session, run `task resume` and continue only from a current executable Handoff.

A Context Pack may be printed or exported for convenience. It is not bound to the Handoff and never controls freshness.

### Reuse a decision

1. Leave routine detail in archived Task history.
2. When a conclusion is likely to matter across tasks, save one Knowledge record with its reason and sources.
3. Query it directly or through explicit Context history modes later.
4. Correct it by saving a replacement that names the old record.

The save itself completes the operation. There is no new candidate, approval, refresh, or rendered-wiki lifecycle.

## Requirements

### Task lifecycle

- Keep live registry in `docs/BOARD.md` and authoritative lifecycle status in Task frontmatter.
- Require `task start` before product mutation and keep the selected repository immutable for that execution.
- Record one Task-owned Chosen set as the current scope source.
- Compare actual changes with Chosen at doctor/finish.
- Protect paths dirty before task start unless ownership is explicitly resolved.
- Preserve optional Verification prose without parsing, grading, hashing, or requiring it.
- Archive standalone finished/canceled Tasks and keep completion receipts immutable.
- Use new follow-up Tasks rather than reopening completed identities.

### Handoff

- Preserve exactly four human-written fields.
- Keep generated placeholders visibly inactive until reviewed and bound.
- Report freshness separately from repository lifecycle health.
- Invalidate a binding when its Task, Handoff, Chosen/Notes, log, optional Verification text, child state, or observed repository state changes.
- Never parse or execute the stored first command.

### Repository understanding

- Keep direct reads, exact search, Context, and Graph as independent entry points.
- Return source paths, locations, typed relations, provenance, freshness, completeness, and useful continuations.
- Keep ranked lexical candidates separate from confirmed Graph relations.
- Exclude stale Graph relations while allowing current-source overlays.
- Keep ordinary query cost independent of completed-task count.
- Never turn retrieval output or old task history into current scope, ownership, or authority.
- Do not persist Context or Graph result receipts for later selection.
- Keep Context Pack generation read-only and one-shot.

### Knowledge

- Store explicit decisions, invariants, and failure modes under `docs/knowledge/records/`.
- Require a claim, concrete reason, repository, and at least one source.
- Keep applicability, replacement links, and known author optional.
- Make the record queryable immediately after one save.
- Warn when a source changes while retaining the recorded historical reasoning.
- Preserve older Knowledge files and report them for one-time agent migration; query only current records.

### Repository and metadata boundaries

- Keep root control state separate from product Git roots.
- Require explicit repository identity when a workspace contains several product repositories.
- Keep `.repometa` as the canonical sparse file metadata store and mutate it through repoctl.
- Keep Graph/index state derived and rebuildable.
- Treat stale Graph or Knowledge projection after upgrade as maintenance, unless source authority or repository identity is actually invalid.

## Success criteria

- A new agent can recover current work and the next action from Task and Handoff without chat history.
- A known small change needs only create/start, Chosen, ordinary implementation, and finish.
- Missing Verification prose or an unrecorded test result never blocks finish.
- Context returns a compact useful first reading set and does not create persistent query bookkeeping.
- A Context Pack can be printed or exported without affecting any lifecycle state.
- Saving reusable Knowledge is one operation and later queries keep the conclusion available after source drift with a warning.
- Product work cannot silently consume pre-existing dirty files or cross a selected repository boundary.
- Completion and history remain usable for follow-up inspection without becoming a test-proof system.
- Upgrade preserves accumulated Task logs and older Knowledge files while reporting the one-time updates needed before they re-enter current execution or search.
- Upgrade succeeds when authoritative state is healthy even if derived indexes need an explicit refresh.

## Non-goals

- Autonomous agent execution.
- Commit, push, pull request, deployment, or delivery ownership.
- Proof that a command or test ran.
- Mandatory Context, Graph, Pack, Knowledge, or debug use.
- Automatic task creation, scope inference, or Knowledge creation from PRD, Backlog, query, or Task prose.
- Online learning from agent behavior.
- A generated documentation or llmwiki subsystem.
- Replacing source authority with metadata, Graph, Context, completion history, or Knowledge.

## Adoption rule

After copying the template, replace or remove this file. If private context grows, keep `docs/PRD.md` as a short index and put detailed authority under `docs/prd/`.
