# Graph Requirements

Graph answers work-oriented questions with current source paths and typed provenance: entrypoint, implementation, dependencies/callers, and relevant tests. Confirmed relations remain separate from lexical candidates.

Snapshot relations, provider artifacts, and text/evidence index are separate capabilities. A missing optional provider cache or evidence index must not erase readable snapshot relations. Features that require a missing component report typed partial/unavailable status.

Current-source overlay excludes stale stored relations. Ambiguity fails closed. Unsupported, partial, ambiguous, not-found, and zero-impact are distinct. Benchmarks judge whether representative project navigation is recovered, not edge count or response bytes alone.

## Current Graph navigation interfaces

`graph query` reads the typed snapshot; `graph workflow --route /predict` or `--symbol audio_clock` produces ordered implementation, caller/dependent and test evidence without promoting candidates to confirmed relations. `graph search` retrieves bounded current-source and authority evidence. `graph workflow-benchmark` tests required stages and provenance, false positives and first-path correctness separately from candidate-inclusive recall. AST adapters are optional and expose partial coverage when resolution is not semantically provable.

## Work-path success and provenance

`graph workflow` returns entrypoints, implementations, callers/dependents and tests separately. Compiler/AST-confirmed edges are typed and distinguishable from textual candidates. A required but ungrounded stage is **not evaluated**, not an empty-success stage. Benchmarks include negative and stale cases, first action correctness, false-positive paths, provenance and required-stage precision; path-inclusive recall alone is never completion proof.

Document authority reflects declared adoption status as well as location: an explicitly draft/proposed requirement cannot be promoted to adopted authority by its filename.

## Source-grounded relationship extensions (post-v0.14.1, source branch)

The optional Tree-sitter adapter now constructs `same_package` Go direct CALLS only between uniquely identified top-level functions in the same directory and declared Go package, excluding shadowed receiver/binding names. Exact Java/Kotlin source `import package.Type` declarations yield `IMPORTS_FILE` (and test-to-file edges for test sources) only when the package and declared top-level type match exactly and the target is unique. Wildcard, static, alias and ambiguous type imports are **not confirmed**. Rust embedded `#[test]` functions with a component name can be surfaced as explicit candidates even when module-definition relations are already confirmed.

Dart's ordinary relative imports and `part 'path.dart'` / reciprocal `part of 'owner.dart'` declarations are resolved as source-file relationships, with a syntactically verified two-hop test→library→part relationship. Directives embedded in comments or strings cannot create confirmed edges. A changed code-index schema invalidates stale import information.

For Python Flask route navigation, an AST-verified factory assignment and blueprint registration can explain why `current_app.model_manager.method` points toward a specific class, but dynamic runtime dispatch is labeled `source` and **never falsely promoted to a compiler-confirmed CALLS edge**. Tests asserting a matching HTTP route without typed Graph provenance remain candidates.

For Rust, an explicitly declared module `mod child;` and unique sibling `child.rs`/`child/mod.rs` can also establish a cross-file `CALLS` relation for `child::public_fn()` when the corresponding target has exactly one public top-level function declaration. The analyzer does not guess trait methods, type dispatch or imports without a declared module. A standalone `_test.go` is classified as a Go test even when stored beside production code. Importing a Python implementation is not itself sufficient for test relevance: route-focused navigation requires actual test-function AST calls to the specific operation/route, not just similarly named files or a cache-maintenance test. Flask wiring may be marked `source` only when the manager assignment and blueprint registration occur within the same app factory, and remains non-compiler-confirmed.
