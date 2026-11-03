# One-time adopter transition — only after verified source release and approval

This workflow **supersedes the earlier v0.14.3 rollout instructions**; neither v0.14.3 nor its old Knowledge-preservation steps are the approved next transition target. No consumer rollout has started. Use this only after the source Main completes every acceptance item, publishes and verifies a *new* release, and the user authorizes a selected adopter. Never apply it to TabletAuto. Protect adopter-owned data, existing dirty files, Task status, product docs, and independent product Git roots.

## 1. Verify a pinned source and protect adopter state

After acceptance, pin the new release tag, archive, size and SHA-256 from the source Main's final report; do not reuse the v0.14.3 archive or commands as a deployment target. Extract to a separate directory, not over the current workspace. Back up adopter-owned root docs, current and historical Task/Knowledge, `.repoctl-state` (including ignored receipts, migration witnesses, manifests, projections), and root Git state together. Record the product repository's existing dirty baseline; never reset product Git roots.

```bash
ADOPTER=/absolute/path/to/selected-consumer-workspace
SOURCE=/absolute/path/to/verified-new-source-release-extraction
PLAN=/absolute/path/to/reviewed-upgrade-plan.json
"$SOURCE/scripts/repoctl" upgrade plan --workspace-root "$ADOPTER" --from "$SOURCE" --output "$PLAN" --json
# Review exact operations, owner-owned create files and missing archive identities before apply.
"$SOURCE/scripts/repoctl" upgrade apply --workspace-root "$ADOPTER" --plan-file "$PLAN" --json
# The immediate postflight is diagnostic; finish each source transition before final acceptance.
cd "$ADOPTER"
```

If the **read-only upgrade plan** finds an `archive_locator_migration_conflict`, stop without applying and have the adopter owner resolve the missing predecessor Task/archive identity from real evidence; never synthesize a replacement archive or rewrite a receipt. The preceding source audit found this blocker in Hither and HostSpan; its historical 28/30 plan result is not proof that those workspaces are now fixed. If postflight finds an archived/closed-task receipt conflict, halt rather than rewriting the receipt to today's hash.

## 2. Transition historical completion and start evidence

The current runtime accepts v5 completion receipts and v4 Task start states only. After upgrading the tool, inspect historical sources *before* treating derived Graph/history indexes as valid. Preview first; apply only independently reviewed records, one Task ID at a time:

```bash
./scripts/repoctl task migrate-receipts --json
./scripts/repoctl task migrate-receipts T-YYYYMMDDHHMMSSZ --json
./scripts/repoctl task migrate-receipts T-YYYYMMDDHHMMSSZ --apply --json
# Only when this old receipt has a reviewed current v2 Knowledge citation; finalize v1 (§4) first:
./scripts/repoctl task migrate-receipts T-YYYYMMDDHHMMSSZ --relink-knowledge --json
./scripts/repoctl task migrate-receipts T-YYYYMMDDHHMMSSZ --relink-knowledge --apply --json
./scripts/repoctl task migrate-states --json
./scripts/repoctl task migrate-states T-YYYYMMDDHHMMSSZ --json
./scripts/repoctl task migrate-states T-YYYYMMDDHHMMSSZ --apply --json
```

Receipt conversion retains the **exact original receipt bytes** and recorded Task-content hash under `docs/archive/task-evidence/receipts/` with a SHA-linked witness. Existing event times and path transitions remain unchanged when recorded. Older v2 receipts that never contained start/completion events or path transitions can become current v5 records with `historical_transition_evidence.status=not_recorded`; this is an explicitly incomplete historical interval, **not inferred proof**. A partially populated or inconsistent set of event fields is unresolved. Compare `historical_evidence_gap_count` (source never recorded the fields) separately from `source_problem_count` (missing or changed actual Task evidence). A schema translation cannot repair pre-existing Task digest drift: stop on that integrity warning. Never replace the recorded content hash with today's Task hash.

Only after a closed Task has a v5 completion authority may its old v3 start-state be retired. `migrate-states` preserves exact original state bytes under `docs/archive/task-evidence/start-baselines/` and removes only the retired active state. Live v3 state, missing Task identity, and cited source hashes are unresolved; do not restart an active Task, infer old dirty state, or force a new baseline. For a cited receipt, first finalize active v1 through §4, then preview the individual `--relink-knowledge` command. It retargets only matching SHA-verified current v2 source refs to the byte-identical archived receipt, without changing claims, reasons or original timestamps. An active v1, mismatched source SHA, transitive Knowledge citation or concurrent change is a blocker. If rollback cannot undo its own Knowledge write after an external edit, it preserves that edit **and the original archived receipt** and reports a conflict; reconcile them before retrying. A cited old start-state still requires its own explicit reviewed source fix. Rebuild Knowledge projection after successful relinking and completion history using each exact repository selector in `history_rebuild_commands`; a workspace-only receipt uses `--workspace`.

## 3. Explicitly clean known Task-generated slop

```bash
./scripts/repoctl task migrate-slop --include-archived --json
./scripts/repoctl task migrate-slop --apply --json
# For each individually reviewed done, canceled or archived Task:
./scripts/repoctl task migrate-slop T-YYYYMMDDHHMMSSZ --closed --json
./scripts/repoctl task migrate-slop T-YYYYMMDDHHMMSSZ --closed --apply --json
./scripts/repoctl task doctor T-YYYYMMDDHHMMSSZ --json
```

Default apply **never** edits a done/canceled child just because it remains in `docs/tasks/`. One explicit `--closed` Task ID preserves its completion-time bytes in an ignored migration witness and checks the original receipt; receipt/catalogue hashes are not re-sealed. Do not run `--closed --apply` on a Task whose original proof is already mismatched. A proof is not new test/finish evidence. Inspect the precise Execution Log changes, including code fences and authored notes.

## 4. Convert and finalize Knowledge v1 with original provenance

```bash
./scripts/repoctl knowledge migrate --repo-id main --json
# For each verified v1 ID, first inspect exactly one proposed v2 successor:
./scripts/repoctl knowledge migrate --repo-id main --record-id K-... --json
./scripts/repoctl knowledge migrate --repo-id main --record-id K-... --apply --json
# If reason/source is missing, supply only real independently verified evidence:
./scripts/repoctl knowledge migrate --repo-id main --record-id K-... --reason 'Verified technical reason' --source docs/specs/capability/requirements.md --apply --json
# Inspect the successor, old citations, scope narrowing and historical Task evidence.
./scripts/repoctl knowledge migrate --repo-id main --record-id K-... --finalize --json
./scripts/repoctl knowledge migrate --repo-id main --record-id K-... --finalize --apply --json
./scripts/repoctl knowledge check --repo-id main --json
./scripts/repoctl knowledge rebuild --repo-id main --json
```

Replace `main` with the actual repository ID; use `--workspace` for root-only records. Stage/finalize **one reviewed ID at a time**, not a blind batch. The first `--apply` may return a nonzero *incomplete transition* status if an existing v2 still `replaces` the active v1 ID: do not add runtime legacy support or try to repair its derived projection early. Review the preview, then complete `--finalize --apply` and rerun the check. An actual missing reason/source, conflicting historical SHA, ambiguous successor, unsafe citation or transitive v2 reference is a blocker and must not be fabricated or ignored.

Finalization retains the **exact v1 bytes** under `docs/archive/knowledge/<record-id>.json`, rewrites only verified SHA-linked v2 references, updates affected v2 digests, and removes the now-duplicate temporary witness. No v1 remains under active `docs/knowledge/records/` after a successful conversion. Inspect `authored_markdown_links` in the preview for exact paths and lines; fix Task/ADR/PRD/Workflow links deliberately after reviewing their meaning. The command never rewrites examples, prose or product-specific decisions. Do not leave a shadow copy or pretend the old path is canonical. Historical Task receipt drift remains visible and may require owner review; migration is not new task verification.

If another writer changes a v2 after finalization's own write and a later step fails, rollback **must not erase that edit**. `knowledge_migration_rollback_incomplete` stops the transition and names the conflicting file. The exact v1 may remain at both the active path and historical archive so the externally edited v2 citation still resolves. Preserve both and reconcile the conflict explicitly before retrying; do not force-clean it as a successful migration.

## 5. Migrate old project documents and references

The current root entrypoint is `docs/PRD.md` and the optional current paths are `docs/architecture/`, `docs/specs/<capability>/`, `docs/adr/`, and `docs/research/`. Old `docs/prd/**` is **not indexed or treated as current product authority**. Copy valuable authored text to the correct current document, review its adoption status, fix references in active Tasks/Knowledge/Workflows, then delete the obsolete path only with the adopter's approval. Avoid empty scaffolding and shadow copies. Do not overwrite adopter-authored `AGENTS.md`, Workflows, `docs/PRD.md`, README files, `.gitignore` or local tool configuration automatically. The v0.14.1 manifest classifies local entry docs/settings as create-only; review existing content and reconcile with local policy first.

## 6. Rebuild derived evidence, run final postflight, and inspect a Task

```bash
./scripts/repoctl check --json
./scripts/repoctl history rebuild --repo-id main --json
./scripts/repoctl upgrade postflight --json
./scripts/repoctl meta check --json
./scripts/repoctl graph build --repo-id main --rebuild --json
./scripts/repoctl graph search 'next implementation and tests' --repo-id main --json
./scripts/repoctl graph workflow --symbol <known-symbol> --repo-id main --json
./scripts/repoctl task resume T-YYYYMMDDHHMMSSZ --json
```

The optional Tree-sitter structural adapter resolves syntax-level relations for Rust, Go, Java/Kotlin, Swift when installed. It does not prove type-dispatched or all cross-file calls. Inspect candidate and confirmed relationships separately and never treat missing tests as zero impact. Current scripts must call `graph search`, `graph query` or `knowledge query` according to needed evidence; removed commands are not aliases.

## Rollback / stopping conditions

Before apply, keep a trusted adopter-specific backup of root docs, original Task/Knowledge files, ignored `.repoctl-state`, `docs/archive/task-evidence/`, and the root Git revision. If upgrade fails, restore the root control-plane **and** its matching Task, state, receipt, historical evidence, Knowledge and catalogue together from that snapshot; do not restore only the Markdown while keeping later receipts/projections. Product repositories remain separate Git roots and should never be reset as a side-effect. If an individual conversion reports a conflict, preserve its original backup/witness for audit and stop. After restoration, rebuild only derived Graph/search and Knowledge projections from the restored source. An outdated derived SQLite schema is not a supported normal read source; rebuild before treating the index as current. Neither `v0.13.0` nor `v0.14.3` is this transition target.

Do not interpret a failed rollback as a clean restoration. `upgrade_rollback_incomplete`, `knowledge_migration_rollback_incomplete`, `receipt_transition_rollback_incomplete`, and `state_transition_rollback_incomplete` mean that concurrent owner content or an unverified write was preserved. Inspect the exact paths, original archive/backup and current bytes, reconcile deliberately, and only then retry. In particular, an externally changed completion receipt or start-state must not cause its byte-identical historical source to be discarded.

### Verify a stale Graph refresh only in an owner-approved target

In an already backed-up adopter workspace, a deliberately stale Graph can be inspected with read-only `graph workflow`; the owner may then run `graph workflow-benchmark --case-id <case> --fixture <path> --repo-id <id> --verify-refresh --json`. **This flag rebuilds the selected workspace's derived Graph cache**, not the product source. Its `refresh.source_rechecked` proof is valid only when a previously admitted stale source becomes current with a different validated Graph digest. A missing Dart compiler or unresolved cross-crate test remains partial, never invented as confirmed. Do not use the flag to modify adopter caches during template-source-only audits; use an isolated copy instead.
