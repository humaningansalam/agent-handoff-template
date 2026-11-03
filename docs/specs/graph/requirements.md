# Graph Requirements

Graph answers work-oriented questions with current source paths and typed provenance: entrypoint, implementation, dependencies/callers, and relevant tests. Confirmed relations remain separate from lexical candidates.

Snapshot relations, provider artifacts, and text/evidence index are separate capabilities. A missing optional provider cache or evidence index must not erase readable snapshot relations. Features that require a missing component report typed partial/unavailable status.

Current-source overlay excludes stale stored relations. Ambiguity fails closed. Unsupported, partial, ambiguous, not-found, and zero-impact are distinct. Benchmarks judge whether representative project navigation is recovered, not edge count or response bytes alone.

## Current Graph navigation interfaces

`graph query` reads the typed snapshot; `graph workflow --route /predict` or `--symbol audio_clock` produces ordered implementation, caller/dependent and test evidence without promoting candidates to confirmed relations. `graph search` retrieves bounded current-source and authority evidence. `graph workflow-benchmark` tests required stages and provenance, false positives and first-path correctness separately from candidate-inclusive recall. AST adapters are optional and expose partial coverage when resolution is not semantically provable.

## Work-path success and provenance

`graph workflow` returns entrypoints, implementations, callers/dependents and tests separately. Compiler/AST-confirmed edges are typed and distinguishable from textual candidates. A required but ungrounded stage is **not evaluated**, not an empty-success stage. Benchmarks include negative and stale cases, first action correctness, false-positive paths, provenance and required-stage precision; full recall or correct required paths with extra false-positive paths cannot count as task-navigation completion.

For Dart `part` files, a test importing the exact owning library is a verified **library/part compile dependency**, not automatically a direct behavioural assertion of the selected extension. The fixture records these separately as `expected_related_paths`; only source-confirmed `TESTS_FILE through exact Dart library/part chain` entries qualify. Direct target tests remain first in navigation. Unrelated or candidate-only tests still fail false-positive checks. A proven stale→current refresh may legitimately return `found` rather than only `partial`.

Document authority reflects declared adoption status as well as location: an explicitly draft/proposed requirement cannot be promoted to adopted authority by its filename.

## Source-grounded relationship extensions (post-v0.14.1, source branch)

The optional Tree-sitter adapter now constructs `same_package` Go direct CALLS only between uniquely identified top-level functions in the same directory and declared Go package, excluding shadowed receiver/binding names. Exact Java/Kotlin source `import package.Type` declarations yield `IMPORTS_FILE` (and test-to-file edges for test sources) only when the package and declared top-level type match exactly and the target is unique. Wildcard, static, alias and ambiguous type imports are **not confirmed**. Rust `#[test]` bodies structurally referencing a declaration in their own implementation module are source-verified test locations; same-word tests in unrelated crates do not become test relations.

Dart's ordinary relative imports and `part 'path.dart'` / reciprocal `part of 'owner.dart'` declarations are resolved as source-file relationships, with a syntactically verified two-hop test→library→part relationship. Directives embedded in comments or strings cannot create confirmed edges. A changed code-index schema invalidates stale import information.

For Python Flask route navigation, an AST-verified factory assignment and blueprint registration can explain why `current_app.model_manager.method` points toward a specific class, but dynamic runtime dispatch is labeled `source` and **never falsely promoted to a compiler-confirmed CALLS edge**. A literal HTTP test-client request to the exact route (including an optional query string), or a call on a locally constructed class imported from the exact implementation module, is AST-verified **source** test evidence. Neither is falsely called a compiler-confirmed edge.

For Rust, an explicitly declared module `mod child;` and unique sibling `child.rs`/`child/mod.rs` can also establish a cross-file `CALLS` relation for `child::public_fn()` when the corresponding target has exactly one public top-level function declaration. The analyzer does not guess trait methods, type dispatch or imports without a declared module. A standalone `_test.go` is classified as a Go test even when stored beside production code. Importing a Python implementation is not itself sufficient for test relevance: route-focused navigation requires actual test-function AST calls to the specific operation/route, not just similarly named files or a cache-maintenance test. Flask wiring may be marked `source` only when the manager assignment and blueprint registration occur within the same app factory, and remains non-compiler-confirmed.

## Explicit stale→current refresh verification (v0.14.2)

Normal `graph workflow` and `graph workflow-benchmark` never write source or claim a refresh merely because current files are available. `graph workflow-benchmark --verify-refresh` is an **explicit mutation of ignored derived Graph cache only**; it must inspect an admitted prior snapshot, observe stale source matching the exact requested `--in-file`, rebuild the selected target and compare validated snapshot digests/freshness. `refresh` is precise only when prior status was stale, the requested source had changed and after status is current with a genuinely different Graph snapshot. When the initial graph is already current, the file is not an exact stale source, or the native analyzer is unavailable, the flag must not invent a completed semantic relation or rerun unsafe product commands. Result includes before/after statuses/digests and whether derived state was updated. This operation requires deliberate user invocation in an adopter workspace; source-release audits must use disposable copies only.

Large compact Graph-search responses use an adaptive excerpt-only byte budget, preserving source identity, typed edges, ranked groups and continuations; retrieval benchmarks must retain the same source recall/precision and gate threshold. No hidden relaxation of benchmark limits is permitted.
