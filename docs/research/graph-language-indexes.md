# Graph Language Index Reuse Research

This document is supporting research, not current product authority.

LSP servers offer broad language-native symbol/navigation behavior but vary in headless workspace setup and stable export. SCIP provides a compact cross-language index model where mature indexers exist, but coverage and installation differ by language. Tree-sitter is lightweight for syntax and local structural extraction, but precise resolution and call graphs require language-specific semantic work.

The smallest maintenance surface is an adapter boundary: accept optional external indexes or LSP-derived facts, normalize them to the existing node/edge/source model, retain built-in inventory/partial parsing, and report precision per language/provider. Do not require every tool, and do not reimplement every compiler frontend. Rust, Go, Java/Kotlin, and Swift should remain `partial` until a verified precise adapter is available; Dart and existing precise providers retain their current relations.

## v0.13 implementation boundary

The release reuses current confirmed Graph symbols, CALLS, IMPORTS_FILE, and TESTS_FILE relations; a bounded read-only source adapter supplies route decorators and explicitly **candidate** cross-file/test hints when a compiler relation is unavailable. No external language server is installed, and this does not claim precise Rust/Go/Java/Kotlin/Swift call resolution. A later provider-backed adapter may use SCIP or language-native indexes only after measured precision and maintenance-cost validation. No new Graph/Task lifecycle or external execution service is created.
