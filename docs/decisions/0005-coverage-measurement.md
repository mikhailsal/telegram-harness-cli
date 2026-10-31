# 0005 Coverage measurement paths

Status: accepted (P00); amended in P07 and P08. Each path below was
exercised on 2026-10-20 with the pinned versions in a scratch project (spikes
are not tracked; their findings are summarized here and become acceptance
tests in P05–P08).

## Common model

Every adapter produces, per source file: executable lines with hit counts and
branch opportunities with hit flags, in the native tool's own semantics. The
core keeps line and branch counters separate, compares exact integers, and
never merges languages. An expected source inventory is computed from the
candidate tree and reconciled with the report; a missing file is an error.

## Python: coverage.py 7.16.2

- Run: `python -m pytest --cov=<source roots> --cov-branch
  --cov-report=json:<fresh path> --junitxml=<fresh path>`.
- Report: JSON format 3. Per file `executed_lines`, `missing_lines`,
  `executed_branches`, `missing_branches` (arcs).
- Unimported files under `--cov` source roots are reported with zero hits
  (verified).
- Native semantics: a branch is a possible jump between lines (an arc).
  Boolean short-circuit operands and conditional expressions on one line are
  not separate branches.

## TypeScript: Vitest 5.0.5 with V8 provider

- Run: `vitest run --coverage --coverage.reporter=lcovonly
  --coverage.reporter=json` with `coverage.include` listing the component's
  source globs for `.ts`, `.tsx`, `.mts`, `.cts`, and the JUnit reporter.
- Report: LCOV (`DA`, `BRDA`, `BRF`, `BRH`) remapped to original sources;
  emitted JavaScript is not counted because only source globs are included.
- Unimported files appear with zero hits only through `coverage.include`
  (Vitest 5 has no `all` option). Type-only files report zero lines and are
  `NOT_APPLICABLE`.
- Vite 8 does not transform `.cts` by default; the profile sets
  `oxc.include` to cover `.cts`. Aliases use `resolve.tsconfigPaths`.
- Native semantics: Istanbul branch types `if`, `cond-expr`, `binary-expr`
  (`&&`, `||`, `??`), `switch`, `default-arg`.
- Zero test files exit 1; the adapter treats it as `tests.none`.

## Dart: package:test and package:coverage (SDK 3.13.6)

- Run per package: `dart test --platform vm --branch-coverage
  --coverage=<fresh directory> --file-reporter json:<fresh path>`, then the
  component's locked package:coverage: `dart run coverage:format_coverage
  --lcov --in=<directory> --out=<fresh lcov> --report-on=<each source root>`
  without `--check-ignore` (amended in P07: `--coverage-path` reports only
  `package:` libraries, so scripts in `bin/` were never measured).
- Report: LCOV with `DA` and `BRDA` but no `BRF`/`BRH`; the parser derives
  totals from the `BRDA` records.
- Unimported libraries do not appear. The adapter generates, inside the
  snapshot only (under `.dart_tool/quality`), a test that imports every
  non-`part` library of the source roots, so the VM force-compiles them and
  reports zero hits (verified). A file still missing after a clean run has
  no coverable line and is not applicable.
- Native semantics: a line is reported only where code enters a function or
  makes a call; a literal `return` or an assignment of a literal or local has
  no line. A branch is a VM basic-block entry (function entry, then/else
  blocks at their opening lines, switch cases, loop bodies, catch blocks).
  `&&`, `??`, and conditional expressions are not instrumented; an `if`
  without `else` has no implicit-else opportunity. This limitation is
  declared, not hidden ([decision 0012](0012-dart-profile.md)).
- Coverage needs a loopback VM service (ephemeral `127.0.0.1` ports); this is
  the permitted instrumentation IPC.
- Zero tests exit 79 and a missing suite root is a failing load case; both
  are errors.
- Flutter is detected from `flutter: {sdk: flutter}` dependencies, an
  `environment.flutter` constraint, or a top-level `flutter:` section and is
  reported as an unsupported variant.

## PHP: PHPUnit 13.4.3 with Xdebug 3.5

- Run: the component's PHPUnit with `XDEBUG_MODE=coverage`, Xdebug loaded with
  `-d zend_extension=xdebug` unless the system loads it, and
  `--configuration=<generated> --log-junit=<fresh path>
  --coverage-clover=<fresh path> --coverage-jsonl=<fresh directory>`
  (amended in P08). The generated configuration filters every `.php` file of
  the source roots without the test roots and `vendor/`, with
  `includeUncoveredFiles`, `branchCoverage`, and `disableCodeCoverageIgnore`
  on.
- Report: both reports come from php-code-coverage 14.4.1, pinned with
  PHPUnit. Neither holds both counters exactly.
  - Clover has per-file `conditionals`/`coveredconditionals`, but writes a
    method's record in place of the statement record of the line where the
    method starts, so a one-line method's statement has no record.
  - The JSONL report has every executable line, but counts branches only for
    functions with more than one.

  Lines come from JSONL and branches from Clover, and the two are
  cross-checked. Cobertura and `--coverage-xml` do not preserve per-file
  branch counts, so they are not used.
- Uncovered files are included for lines, but a file that is never compiled
  has no branch data. The generated bootstrap loads every source file that
  only declares, inside the snapshot, before the tests. A file that runs code
  when loaded is left to the tests. After the run, a file with functions or
  methods but no branch is unmeasured and fails.
- PHPUnit refuses to start when no driver supports branch coverage, as under
  PCOV, phpdbg, or no driver. It exits 1 with a runner warning and leaves an
  empty JUnit file, which the adapter treats as a run that could not start.
  The runtime probe already rejects a PHP without Xdebug
  ([decision 0013](0013-php-profile.md#runtime)).
- Native semantics: a branch is an Xdebug basic block, counted when it is
  entered. An `if` without `else` whose condition is true enters every block,
  so only a short-circuit operand, a ternary arm, or a `match` arm that is not
  taken leaves a branch missed while every line is covered.
- Skipped and incomplete tests are both skipped in the JUnit output, which
  replaces the Open Test Reporting output planned here.

## Evidence rules

Each run gets a new empty output directory; reports must exist after the
runner exits, must be parseable, must reference only snapshot files, and are
discarded if the runner failed. Zero executed tests in a declared unit suite
fail. Integration and end-to-end runs never feed unit thresholds.
