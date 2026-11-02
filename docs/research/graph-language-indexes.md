# Graph Language Index Reuse Research

This document is supporting research, not current product authority.

LSP servers offer broad language-native symbol/navigation behavior but vary in headless workspace setup and stable export. SCIP provides a compact cross-language index model where mature indexers exist, but coverage and installation differ by language. Tree-sitter is lightweight for syntax and local structural extraction, but precise resolution and call graphs require language-specific semantic work.

The smallest maintenance surface is an adapter boundary: accept optional external indexes or LSP-derived facts, normalize them to the existing node/edge/source model, retain built-in inventory/partial parsing, and report precision per language/provider. Do not require every tool, and do not reimplement every compiler frontend. Rust, Go, Java/Kotlin, and Swift should remain `partial` until a verified precise adapter is available; Dart and existing precise providers retain their current relations.

## v0.13 implementation boundary

The release reuses current confirmed Graph symbols, CALLS, IMPORTS_FILE, and TESTS_FILE relations; a bounded read-only source adapter supplies route decorators and explicitly **candidate** cross-file/test hints when a compiler relation is unavailable. No external language server is installed, and this does not claim precise Rust/Go/Java/Kotlin/Swift call resolution. A later provider-backed adapter may use SCIP or language-native indexes only after measured precision and maintenance-cost validation. No new Graph/Task lifecycle or external execution service is created.

## Structural adapter implemented

`tree_sitter_structural` is an optional language-pack-backed adapter for Rust, Go, Java, Kotlin and Swift. It extracts real syntax definitions, ranges, unique unqualified same-file calls, and Rust module-to-file relations; it does **not** resolve compiler/type-specific overloads, receiver dispatch, or arbitrary cross-file calls. This is useful structural evidence and deliberately conservative precision, not universal language parity. The core CLI works without `structural`; to enable its AST provider install the `structural` extra (`uv run --extra structural ...` for repository development). Tests for each language and ambiguous dispatch are part of the release gate.

## Validated native-adapter scope (v0.14)

The optional `tree-sitter-language-pack` adapter is implemented rather than only proposed. It supplies bounded, source-ranged syntax definitions, unique same-file unqualified call edges and Rust `mod` declarations, across Rust/Go/Java/Kotlin/Swift. It deliberately omits unproved cross-file dynamic/type-dispatched calls. Kinport's three real Rust source files exposed a segfault in recursive native `Node`/`Point` traversal; iterative indexed child walking and byte-offset line conversion resolve this case, with a large-source regression fixture. LSP and SCIP remain evaluated future adapter options, not installed runtime prerequisites. Compiler-grade precision is not claimed for languages without a verified compiler provider.

## v0.14.1 false-positive controls

Structural parsing is not type resolution: a same-name Rust/Go closure or parameter can shadow a global function, and an unqualified call may not target an unrelated `impl`/receiver method or a Java class's method. The v0.14.1 provider excludes these from confirmed CALLS and retains partial coverage rather than inferring a dispatch target. More complete language-level dispatch still requires a type-aware provider.

## Read-only real-source isolation checks (Graph work continuation)

Four named real-workload cases were tested by copying only selected current source files to disposable fixture workspaces and explicitly building fresh Graph snapshots; adopter repositories and their derived caches were never altered. The Gakza Dart owner→part→test chain can meet the complete required-stage precision criteria when `pubspec.yaml` and the reciprocal library/part files are present. Kinport finds the Rust module implementation and declaring parent with confirmed edges, plus source-anchored candidate embedded unit tests in another crate: those tests do not directly import the target module and are not compiler-confirmed as covering it. Basic ML API finds `/predict`, `ModelManager`, API and concurrency tests; Flask's dynamic `current_app` dispatch remains source evidence rather than a proven runtime binding. Cops-and-Robbers stays partial until an explicit stale→current refresh proof is checked. The acceptance report must separate path recall from confirmed relation precision and not equate the two.

The source-continuation regression suite adds positive and negative examples for Rust `mod`→public direct calls, Go same-package cross-file calls and shadowed parameter false positives, Java/Kotlin exact `package.Type` imports versus wildcard/static imports, and Dart reciprocal `part` directives versus commented-out source text. It also checks the behavior of Flask factory wiring across different functions and rejects unrelated import-only tests. These are syntax-grounded source relations; they do not prove runtime behavior or successful test execution.

An optional `graph workflow-benchmark --verify-refresh` now performs its own stale→current Graph rebuild against the explicitly selected source path, producing a verifiable before/after snapshot-digest proof instead of relying on a manually injected `refresh` object. Only the derived Graph state is written. When a language provider is missing, the rebuild can establish freshness but cannot promote source-only symbol precision. The default path stays read-only. The compact evidence projection also enforces its answer budget by shortening excerpts rather than discarding source refs or their provenance.
