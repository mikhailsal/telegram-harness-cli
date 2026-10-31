# 0012 Dart profile

Status: accepted (P07).

## Scope

The Dart profile checks a Dart VM package with the pinned SDK's formatter
and analyzer, and with package:test plus package:coverage for its suites
([decision 0001](0001-toolchain-pins.md#dart-profile)). The structure metrics
and module architecture are the common ones of
[decision 0007](0007-common-gates.md); the test and coverage engine is
[decision 0008](0008-test-and-coverage-engine.md), and the coverage semantics
are in [decision 0005](0005-coverage-measurement.md#dart-packagetest-and-packagecoverage-sdk-3136).
Flutter packages and browser tests are unsupported variants: the profile
rejects them instead of checking part of them.

The SDK is a pinned tool of `tools/quality/toolchains.toml`, a zip archive
verified by SHA-256 and unpacked whole into the toolchain cache. The pub
packages are pinned in the same manifest under `[packages.pub]`; every
component locks them at those versions in its own `pubspec.lock`, because
the test runner and the coverage formatter run inside the component's
package configuration. The SDK is installed only when a Dart component needs
it.

## Component contract

Before any tool runs, the profile reads the component's configuration from
the candidate snapshot. A component that fails this check is not formatted,
analyzed, or tested. Every finding is `dart.config`, located at the file to
fix.

| File | Requirement |
| --- | --- |
| `pubspec.yaml` | A YAML mapping with a `name`, at the component root |
| Variant | No Flutter (`sdk: flutter` dependencies, an `environment.flutter` constraint, or a `flutter:` section) and no web compilers |
| `pubspec.lock` | In the component root, or, for `resolution: workspace`, at the root of its pub workspace; `test`, `coverage`, and `lints` locked at the pinned versions from a hosted source; path packages inside the repository |
| `analysis_options.yaml` | At the component root and wherever else it appears in the component: includes `package:lints/recommended.yaml`; sets `strict-casts`, `strict-inference`, and `strict-raw-types`; lowers no diagnostic to `ignore` or `info`; turns no lint rule off |
| `dart_test.yaml` | Only `timeout`, `concurrency`, `fold_stack_traces`, `chain_stack_traces`, and `verbose_trace`: every other key changes which tests run, on which platform, or how they are reported |

## Environments

Every Dart command runs with the pinned SDK first on `PATH`, the shared pub
cache `$XDG_CACHE_HOME/quality/pub`, a private home in the work area, and
analytics off. Each lock directory is resolved once per snapshot work area
with `dart pub get --offline --enforce-lockfile`. Checks never download: a
package missing from the cache is an environment error that names
`./quality bootstrap`, which fills the cache by running the same command
online in a throwaway copy of the lock directory's manifests
([decision 0010](0010-launcher-bootstrap-and-hooks.md)).

## Static checks

The gate passes the component's `.dart` source and test files.

| Rule | Check |
| --- | --- |
| `dart.format` | `dart format --output=none --set-exit-if-changed`; each changed file is a finding |
| `dart.lint` | `dart analyze --fatal-infos --format=json`: diagnostics of type `LINT` |
| `dart.types` | The same analysis: every other diagnostic, compile errors and strict-mode findings included |

Both tools receive the files by absolute path, so `analyzer.exclude` cannot
hide one. The analyzer also receives the component's `pubspec.yaml` and every
`analysis_options.yaml`, because it reports a problem in them, such as an
unknown or deprecated lint, only for a named file; such a finding is
`dart.config` unless it is a lint. Every severity is an error. A finding
outside the component's inventory is located at its `pubspec.yaml`.

## Structure and architecture

Structure and imports come from a helper in `tools/quality/helpers/dart`, a
Dart program built on the pinned package:analyzer. It is compiled once to a
kernel snapshot in the toolchain cache, keyed by the digest of its sources,
lockfile, and the SDK version, because starting it from source costs about
16 seconds. It answers one JSON request per component with the counting rules
of decision 0007:

- functions are top-level functions, methods, accessors, operators,
  constructors (with their initializer lists), local functions, and
  closures; declarations without a body are skipped;
- complexity adds one for each `if` and `if-case`, collection `if` and
  `for`, conditional expression, loop, `catch`, non-default `case`,
  switch-expression arm, `&&`, `||`, `??`, `??=`, and logical pattern; an
  irrefutable final arm without a guard adds nothing;
- nesting counts `if`, loops, `switch`, and `try`; an `else if` chain stays
  at one level;
- a nested function or closure is measured on its own. Its symbol is its
  name qualified by the enclosing types and functions, such as
  `Parser.parse` or `outer.inner`; a closure takes the name of the variable,
  named argument, or assignment target it is given to, else `<anonymous>`.
  A function starts at its first token after documentation comments and
  metadata.

Imports and exports are resolved by the helper: a `package:` URI of the
component's own package becomes a file under `lib/`, a relative URI a file of
the component, and every conditional URI an edge of its own. `dart:` and
other packages are not edges. A `part` file belongs to its library; it is
measured but never imported by the generated coverage test.

## Suites

Unit suites run `dart test --platform vm --no-retry --branch-coverage
--coverage=<raw> --file-reporter json:<results>` over the declared roots and
one generated test, written only in the snapshot under `.dart_tool/quality`,
that imports every non-part library of the source roots. The component's
locked package:coverage then formats the raw data with `format_coverage
--lcov --report-on=<each source root>`, so scripts in `bin/` are measured as
well as `lib/`. `coverage:ignore` comments are never honored. Other suites
run without coverage and without the generated test.

Results are read from the JSON reporter's events. A run without its final
`done` event, a malformed event, or an unknown or repeated test is a report
error. Zero tests fail with `tests.none`, and a missing suite root is a
failing load case; neither is a pass. Coverage is formatted only after a unit
run that exits 0. Every suite runs with `--no-retry`, so a test's `retry` option
cannot turn a failure into a pass. A `solo` test makes `dart test` skip
every other test; the gate reports it as `tests.focused`
([decision 0014](0014-behavioral-scenarios.md)).

After a clean unit run, a source file that the report lacks was compiled
through the generated test and has no coverable line, so it counts as
measured and not applicable, like a TypeScript declaration file.

Each run gets a new home, cache, and temporary directory in the work area.
Coverage needs the VM service on an ephemeral loopback port, the permitted
instrumentation IPC: unit suites reach loopback only
([decision 0008](0008-test-and-coverage-engine.md#network-isolation)), and the
VM service works inside that namespace. The runner kills whatever is left of
its process group when the run exits, so no VM service outlives it.

## Fixes

`./quality fix` runs `dart format` and `dart fix --apply` in a temporary
copy of the component with an offline resolution, then copies back each
changed file whose working-tree content is still the copied one. It never
stages anything.

## Residual risks

- Dart instruments only function entries, call sites, and block entries, so
  a literal `return` or assignment has no line of its own, and `&&`, `??`,
  and conditional expressions are no branch opportunities
  ([decision 0005](0005-coverage-measurement.md#dart-packagetest-and-packagecoverage-sdk-3136)).
  The floors apply to what the VM measures.
- Tests and `analysis_options.yaml` are code reviewed with the change; the
  contract above bounds what the options can weaken.
- The generated code marker recognises build_runner's header, but only an
  explicit `generated` classification removes a file from the gates
  ([decision 0006](0006-discovery-and-classification.md)).
