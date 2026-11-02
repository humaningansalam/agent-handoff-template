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
