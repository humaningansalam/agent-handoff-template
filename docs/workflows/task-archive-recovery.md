# Recovering a missing archived Task authority (read-only preflight first)

This procedure is for an adopter owner reviewing an `archive_locator_migration_conflict`. **The source release never repairs an adopter automatically.** Work from the adopter's authenticated workspace with an independent verified backup. A completion receipt or archive locator is a reference to original bytes, not those bytes themselves.

## Inspection

1. Save an offline backup of `docs/tasks/.repoctl-state/` and the entire `docs/archive/tasks/` tree before considering any changes. Do not edit or regenerate a completion receipt.
2. Run `./scripts/repoctl upgrade plan --from <trusted-source-release-directory> --json`. For a dangling archive locator, the result names its historical Task path and, if one exists, the **unverified SHA-256 claimed by the matching completion receipt**. A plan with conflicts must not be applied.
3. Locate the exact original archived Task in a trusted external backup, earlier image, or other immutable archive. **Do not** reconstruct its narrative from a summary, create a placeholder Task, or use the completion receipt as a substitute. If the original bytes are unavailable, stop and retain the conflict for the owner.
4. The owner should independently verify the recovered file's SHA-256 against the receipt's recorded digest, and inspect its frontmatter (`id`, `status` and canonical Task path) before any deliberate restoration. A mismatched digest requires investigation, not editing the receipt.
5. Only after restoring the exact verified historical Task with an owner-approved procedure should the owner rerun read-only upgrade plan, then upgrade postflight. The current release does not authorize automatic archive recovery.

## Known read-only findings

- **Hither**: `docs/archive/tasks/T-20261013003232Z--multi-os-prd-roadmap-benchmark.md` is missing; its existing receipt claims SHA-256 `bf61dfc467526b3727365a902c665375b6bb3d663231b453c95dba0c3a52f88a`.
- **HostSpan**: `docs/archive/tasks/T-20261023141738Z--consumer-read-recovery.md` is missing; its existing receipt claims SHA-256 `183fb7e914684fe7d7b4ba972fb0c0e5aedb03e1b38ebf7a87efff646b9fe5cb`.

These are receipt-reported hashes, not independently verified contents. Neither original file could be located in the corresponding adopter workspace during the read-only audit, so **neither conflict is resolved**.
