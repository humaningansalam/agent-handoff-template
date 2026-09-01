# repoctl Discovery outcome contract

This contract defines the closed loop between Context or Graph results, task Discovery decisions, verification, completion evidence, and later related discovery.

Discovery records explicit exclusions and selected flat result entries, validates them against the producer receipt, and freezes canonical member capsules into task-owned outcome state. Structured verification and completion receipt outcome capture, catalogue ingress, bounded hot projection, and the ordinary Context current-subject join are implemented. Stored producer result receipts remain flat rather than exposing canonical members directly; Context's versioned public projection does not redefine that manifest. Exact Graph task/artifact lookup and Context `past_decision` / `failure_mode` search are explicit cold-history operations; neither widens the ordinary hot projection.

## Why this boundary exists

A query result records what repoctl returned. It does not prove what an agent reviewed, which result member informed scope, what the agent found outside the result, or which subjects survived verification. Task-owned outcome state and the completion receipt join those facts only after explicit Discovery and verification mutations.

The minimum closed loop is:

```text
typed Context/Graph result members
-> explicit episode review decisions
-> independently discovered subjects
-> structured subject-level verification
-> immutable completion-bound outcome
-> bounded provenance-bearing corroboration in a later related query
```

This is deterministic evidence reuse. It is not a model-training loop and does not maintain global learned weights.

## Canonical subjects and result members

Every citable result member has a canonical subject rather than an untyped authority/ref string. Supported subject kinds include:

```text
file
symbol
document
task
artifact
knowledge
relationship_fact
```

A result member binds:

- one stable member ID inside one exact producer result
- the canonical subject identity and selected repository
- one or more typed claims explaining why the subject appeared
- the subject or relationship fact's own version digest; a whole-Graph snapshot digest is provenance, not subject freshness
- any typed continuations returned with that member

Claims may describe source match, exact identity, provider symbol, Graph relation, task history, prior episode role, or verification outcome. A claim is evidence about a subject; it is not an `owner` boolean.

A task citation identifies the producer, result ID, episode ID, canonical-request digest, exact member ID, and source-receipt digest. When the citation is recorded, Discovery freezes a compact member capsule containing the canonical subject, subject-version digest, and each decision-relevant claim's kind, source/fact ref, and evidence digest. It does not copy excerpts, display metadata, or snapshot-bound continuations. A follow-up result that informs the decision receives its own citation. Deleting the regenerable producer cache must therefore leave the citation independently verifiable.

Each completion receipt keeps a local subject table: repository/result identity and each canonical subject are stored once in that receipt, while episode roles and verification outcomes refer to local subject IDs. This is not a global mutable subject store. The receipt does not duplicate excerpts, rendered selection reasons, commands, or prose per role.

## Episode state

One canonical Candidate query owns one active Discovery episode. Graph follow-up selectors may accumulate under that episode without replacing the seed query.

Episode and task-scope roles are intentionally separate:

```text
active_chosen       current task edit scope; may carry across episodes
episode_reviewed    subjects explicitly inspected for this query
episode_excluded    reviewed subjects explicitly rejected for this query
```

The invariants are:

```text
episode_excluded is a subset of episode_reviewed
episode_excluded and active_chosen are disjoint
```

`active_chosen` is not required to be a subset of the current episode's Reviewed set because Chosen scope may carry across queries. A carried Chosen subject does not become current-episode support until it is explicitly reviewed again.

The final Chosen set and changed-file equality prove the recorded completion scope, not when a path was first selected relative to its first mutation. The current schema carries no first-Chosen preimage or trustworthy mutation timestamp, so repoctl must not infer “chosen before edit” from `actual == chosen`, filesystem times, or final subject versions. Agents still follow the operating order in `AGENTS.md`; any future temporal warning or gate requires explicit forward-captured evidence and a schema migration rather than retroactive scope claims.

Starting a distinct Candidate query retains `active_chosen` and closes the prior episode. An episode with no explicit disposition or citation is discarded; otherwise its canonical subjects, citations, and explicit roles are sealed as a compact prior episode in the task-owned state. The new active episode starts with empty Reviewed, Excluded, result citations, and notes. This preserves an explicitly rejected first result after query refinement without retaining clicks, file-open events, or presentation copies. Not choosing a reviewed subject is not an exclusion; exclusion must be explicit.

The episode fixes one seed result and its candidate-member-set digest. When an agent explicitly records a subject absent from that set, repoctl derives `outside_candidate_set` by comparing canonical subject identities. Graph follow-up results retain their own traversal provenance but do not retroactively widen the seed set. The agent does not assign a subjective `newly_found` status.

Discovery placeholders are structural template state, not a blacklist over user data. Repoctl treats a field as empty only when its sole value is an unquoted supported placeholder such as `none yet`; a backticked value is always explicit data, even when the value is `todo`, `none`, or `pending`. This prevents a legal path or query identity from disappearing during Task/outcome alignment. Once explicit, a Chosen value must canonicalize as a workspace-relative path; absolute, escaping, backslash, empty-normalization, and otherwise non-canonical values are reported as `discovery_task_chosen_invalid` rather than removed from the comparison set.

## Verification and completion

The task lifecycle owns structured verification records. Each executed check binds one evidence ref, a closed status of `passed | failed | mixed | blocked`, and the subject or claim IDs it covers; one record may cover several subjects. An absent record means not run, avoiding per-subject placeholder state. Human Verification prose may cite the same evidence but is not parsed to infer status.

Discovery outcome state schema v2 retains `verification_subjects` as the exact canonical closure of subject capsules referenced by structured verification records. Its subject IDs must equal the union of every record's subject IDs; extra, missing, duplicate, malformed, or version-substituted capsules are invalid. The pool preserves the exact post-rebind version that a check covered, but it is not a Discovery role, does not widen task scope, and cannot by itself make a historical subject eligible for another verification add. Claim IDs remain valid only while they resolve to frozen citation claims.

A valid schema-v1 state is migrated to v2 in memory only after its original digest, structure, and every verification subject and claim reference validate. The read does not rewrite or otherwise upgrade the source file. A well-formed reference that no retained v1 capsule or citation claim can resolve fails as `discovery_outcome_verification_reference_invalid`; repoctl never reconstructs the missing historical version from the current filesystem.

A `todo` task may record ordinary Discovery before start. After its status becomes `doing` or `blocked`, every Discovery mutation requires current task-start evidence with the same immutable repository scope; a legacy live task without that baseline fails before Task or outcome bytes change. Every structured verification mutation requires current start evidence regardless of status, so a pre-start outcome cannot acquire check claims and an unfinishable legacy outcome cannot be extended.

A started root-only workspace task may use `task verification add --artifact <workspace-relative-path>` without first creating product Discovery state. Its immutable start scope must also be root-only; todo tasks, legacy live tasks without that baseline, malformed current state, and product-start tasks later reclassified by frontmatter fail closed and leave no outcome state. Ordinary product Discovery and verification mutations likewise require the current selected repository to match the baseline, so root-to-product reclassification cannot create a new outcome owner. The task-start generation is monotonic for the Task—blocked restart preserves it and no command refreshes it—so add and finish validate the same lineage rather than creating a second mutable generation owner. Repoctl then creates a `repository: null` outcome with a synthetic workspace-verification episode, records each artifact as Reviewed, leaves `active_chosen` unchanged, and binds the check record to the opaque artifact subject identity. The evidence ref itself is content-digested; the artifact subject is not treated as a current file-version claim. The path must resolve to an existing regular file inside the workspace and outside `repos/**`; absolute, non-canonical, missing, directory, product, and escaping-symlink paths fail closed. Repo-scoped tasks use `--subject` and cannot use this artifact path.

Changed current Chosen product files without exactly passed structured verification remain an advisory rather than an inferred failure. `task doctor` reports the coverage while the task is live and may point to `task verification add`. `task finish` repeats the coverage and warning as immutable audit output after closure, but emits no command that would mutate the completed task.

Changed product paths outside the active Chosen set are different: they are a hard closure blocker. Doctor and finish expose the same `actual_changes_outside_chosen` problem and complete `unchosen_actual_paths` input so the operator can add approved scope, revert the change, or move it to a follow-up task.

Task finish freezes the canonical episode projection and structured verification records inside the immutable completion receipt. Projection maps every recorded subject and claim without filtering and must pass `validate_completion_outcome()` before it returns; finish repeats that validation before preparing archive, Board, receipt, catalogue, or sidecar writes. Receipt validation proves the task-owned structured state, referenced evidence, task artifact, and repository identity agree; Markdown prose is not a second structured-state owner. When outcome state exists, every explicit Task `Chosen files` value must be canonical and the resulting projection must name the same identities as `active_chosen`. An invalid Task value, repository-identity mismatch, or Chosen-set mismatch is unhealthy lifecycle evidence and a hard completion gate, not zero structured-verification coverage. Repair uses the normal Discovery mutation boundary so the human projection and machine state commit atomically again.

Legacy completion receipts remain valid for the evidence they contain. They do not acquire invented Reviewed, Excluded, outside-candidate, or verification-status facts.

## Later-query consumption

A later Context query exposes a bounded `prior_task_outcome` lane only when all applicable checks pass:

- same stable repository identity
- valid completion receipt and uniquely resolved immutable task artifact
- current, non-deleted subject identity
- exact current file-version identity for the ordinary hot join
- independent current-source or current-Graph evidence for the subject

Ordinary Context first retrieves its bounded current candidates without consulting task history, then joins those canonical subject identities to the hot outcome frontier. Prior query text and query signatures are provenance only; they are not an eligibility key, a semantic-normalization problem, or a reason to scan cold history. Exact canonical-request digest equality may be displayed as stronger provenance, but it is not required for the subject join.

`past_decision` and `failure_mode` are the explicit Context cold-history selectors. Their bounded matches appear only in `related_history`, after current candidate retrieval, ranking, Graph-anchor selection, and traversal are fixed. They do not add or reorder current candidates, create Graph seeds or relations, infer scope, or contribute to the hot outcome join. Exact Graph `--task` and `--artifact` selectors likewise build an ephemeral projection from the one validated cold record rather than inserting cold history into the active snapshot.

Role semantics are:

- Reviewed-only: neutral historical evidence
- a subject in `episode_reviewed` and `active_chosen` with applicable passed verification: positive corroboration
- Excluded: negative historical evidence for the related episode, never a ban
- Outside candidate set: provenance that current evidence was discovered beyond the original result, not an automatic rank boost by itself

A changed subject makes an old role stale. An unrelated task does not inherit a prior exclusion. Current exact source and typed provider relations remain ahead of historical corroboration.

## Authority boundaries

Episode outcomes do not by themselves create:

- task edit scope or file ownership
- semantic owner, dependency, import, call, or test edges
- Graph traversal seeds
- project authority
- Reviewed Knowledge records
- automatic task mutations or a mandatory exploration order

Reusable cross-task decisions, invariants, and failure modes continue through explicit Knowledge candidate review and approval. llmwiki remains a human-readable projection of approved knowledge and current evidence.

Outcome capture reuses the existing Discovery and finish mutation boundaries. It does not require per-query, per-click, per-file-open, or feature-use logs. When no applicable prior outcome exists, the later result omits the lane instead of paying a fixed explanatory payload. Only a fresh Reviewed/Chosen/not-Excluded/not-outside-candidate role whose verification status set is exactly `[passed]` may act as a weak bounded tie-break; every other historical role remains visible evidence and cannot suppress current evidence or prevent exact search, direct inspection, or another typed traversal.

The immutable receipt keeps the complete canonical outcome. Ordinary Context consumes only the query-applicable `file` portion of the hot projection; other subject kinds remain available from the receipt through explicit cold-history operations. The hot projection coalesces all claims for one retained file version, bounds visible subjects, reports ingestion-maintained truncation, and regenerates current typed continuations on demand. It never scans cold history to compute query-specific “complete” counts. Compression may remove repeated presentation but never turn omission into a negative judgment.

## Retention and compaction

Completed task receipts are cold audit history. Task finish publishes one immutable receipt and one content-bound catalogue entry through the existing lifecycle mutation boundary. Catalogue entries have a monotonic sequence, the prior prefix digest, receipt and artifact-resolution digests, and the outcome subject keys. The catalogue is derived indexing state rather than a second authority. Its checkpoint binds the last sequence, accumulated prefix digest, projector schema, and effective compaction-policy digest. Normal consumers validate that checkpoint and ingest only the catalogue tail; they do not walk or hash the receipt archive. A gap or checkpoint mismatch makes historical projection typed-unavailable until `./scripts/repoctl history rebuild --repo-id <id> --json` explicitly validates a product repository's full receipt authority and rebuilds the catalogue; workspace-scoped tasks use `./scripts/repoctl history rebuild --workspace --json`. Exact cold lookup validates the referenced receipt, while `repoctl check --audit-history` explicitly audits every receipt/task artifact plus catalogue JSONL, sidecars, checkpoint, hot replay, and exact receipt-to-catalogue task-set parity. That explicit audit first validates the committed cold JSONL exactly against its checkpoint and hot projection, then validates any contiguous pending sidecar chain from the checkpoint terminal identity through the current head. Its source parity and terminal summary cover the combined committed-plus-pending chain, but the audit never consumes the tail or writes catalogue state; ordinary `check` remains bounded and may report the same topology as `tail_pending` without scanning cold history.

Only `file` outcomes are eligible for ordinary Context's hot outcome frontier. The stable role key is `file:<repo-relative-path>`, while the hot cell key additionally binds the file subject's version digest. That digest covers canonical path identity, current content, and retrieval classification. Context derives the same subject from the current file and reads only the exact versioned cell, so an edited, deleted, renamed, or recreated path cannot inherit an older outcome. Old cells are harmless bounded derived state until normal frontier retention evicts them; no tombstone or subject-change reverse index is required.

Graph snapshot identity remains lineage provenance, not file freshness. Document, symbol, relationship, task, artifact, and Knowledge subjects remain in the immutable receipt or their existing explicit lifecycle stores; they are not promoted into ordinary Context hot cells. A changed-entry witness for a versioned file outcome is retained in that versioned cell. Only a legacy or no-outcome changed path uses a bounded unversioned `file:<path>` cell so Graph can project structured task/file history without treating that witness as a learned outcome.

One outcome cell is keyed by repository, stable file key, and file version. Its frontier contains at most `max_frontier_per_subject` records. Each record contains only that file's role/verification evidence, an optional changed-entry witness, bounded receipt identity, overflow/truncation counts, the catalogue sequence, and a typed cold continuation. The role map stores the stable file key; validation recomputes the versioned cell key from that role's version digest. Complete outcome role maps and receipt payloads are never copied into a record. Query text, query signatures, episode IDs, and complete receipt-digest lists remain cold provenance. Conflicts remain bounded frontier evidence and never select a semantic winner automatically.

The completion-catalogue compaction policy has required positive finite fields for subjects, frontiers per subject, subjects per event, subject-key bytes, hot-path bytes, individual hot-record bytes, serialized hot-projection bytes, and individual catalogue-event bytes. Its shipped defaults and any override are part of the catalogue policy digest. Event ingress bounds the subject-local witness map by `max_subjects_per_event` and `max_catalogue_event_bytes`; projection retention bounds each subject's records by `max_frontier_per_subject`, every record by `max_hot_record_bytes`, and the complete projection by `max_hot_projection_bytes`. Persisted catalogue state is rejected when any limit is exceeded. Retention is deterministic by catalogue sequence and digest. A missing, zero, infinite, or unknown limit is invalid rather than an unbounded fallback.

Task finish work is proportional to the current task's bounded catalogue event. Normal materialization is proportional to new catalogue entries and the cells named by those entries. Hot index, ordinary query work, and query payload are bounded by the fixed policy—not completed-task count. Ordinary Context derives exact current file-version keys, performs one batched frontier read, and never scans cold receipts. Only explicit recovery may rebuild from all cold receipts. Cold storage may grow by one completion envelope per task; repeated large payloads are content-addressed or referenced rather than copied. Permanent lossless audit and bounded total disk cannot both be promised, so any destructive cold-history retention/export policy is explicit workspace policy.

Context and Graph query receipts are a separate regenerable cache, not cold completion history. The result-receipt module owns its independent positive finite count, byte, and age defaults and validates every explicit collection override; these limits are not completion-catalogue policy and do not enter its digest. Query receipts are unpinned by default; only a citation-copy transaction holds a temporary lease until its member capsule is durably frozen. Write-time collection applies the result-cache defaults and removes expired entries first, then the oldest cache insertion sequence and receipt digest. The machine-owned cache index assigns that sequence; producer request text and filesystem timestamps never define count/byte eviction order. If the regenerable index is absent or invalid, valid orphan receipts are adopted deterministically by receipt digest. Eviction cannot change query correctness, task evidence, or later outcome provenance.

## Acceptance scenarios

The outcome loop is accepted only when field-repository scenarios show all of the following:

1. A verified current owner found outside an original candidate set appears in a later related query with exact episode and verification provenance.
2. An unchanged explicit exclusion remains inspectable, never receives a positive corroboration boost, and becomes stale after content changes; it does not suppress an independently retrieved current candidate.
3. The exclusion has no effect on an unrelated new task.
4. A displayed but unrecorded result member has no effect on later ranking.
5. Legacy receipts contribute no invented outcome facts.
6. Across a related task pair, total capture, receipt, result, reading, and exploration cost is lower than the no-reuse baseline; savings are not claimed from output size alone.
7. The same behavior survives changes in language, framework, extension, and directory naming without project-specific routing rules or a fixed tool sequence.
8. Repeating an equivalent verified episode many times leaves hot-index rows, witness sets, ordinary query work, unchanged-delta refresh work, and later query payload stable; cold storage adds only one completion envelope per task and does not duplicate shared large payloads.
9. Adding old cold receipts without a valid catalogue delta makes the historical projection unavailable rather than invisible, and an ordinary query or incremental refresh never scans the full archive.
10. After edit, rename, delete, or recreation, an ordinary Context query cannot match a stale file-version outcome; bounded retention may keep that cold-derived cell without making it applicable.

If these outcomes do not improve later owner/test/impact hit rate or repeated-discovery cost, the hypothesis is rejected and retrieval/tokenization or continuation presentation must be investigated before adding more outcome schema.
