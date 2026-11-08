# 0014 Behavioral scenarios

Status: accepted (P09).

## Scope

Coverage shows which code ran, not which behaviors work. Every component
therefore declares its capabilities, the boundaries through which others use
it, and a scenario manifest proves each requirement those capabilities bring.
A scenario is a stable ID mapped to native test cases of one suite. It counts
as proved only when the fresh results of this run show those cases executed
and passed. Test names, a manifest, or an earlier run prove nothing.

The rules are in `quality/scenarios/` and the engine glue is in
`quality/engine/scenarios.py`. The manifest schema is
[`scenarios.schema.json`](../reference/scenarios.schema.json), which
`quality policy schema --write` generates with the policy schema.

## Capabilities

A component's `capabilities` in [`quality.toml`](../../quality.toml) list
its boundaries. A component with none states why in `no_boundary_reason`,
for example a pure computation or an internal helper. Declaring neither, or
both, is a policy error. A component with capabilities names its manifest in
`scenarios`, a path relative to its root.

| Capability | Requirements (optional ones in italics) | Suites that prove them |
| --- | --- | --- |
| `library` | `library.public-api` | any |
| `cli` | `cli.smoke` (the built artifact starts and answers) | e2e, acceptance |
| | `cli.flows` (declared critical flows) | integration, e2e, acceptance |
| `http-api` | `http-api.success`, `http-api.contract`, *`invalid-input`*, *`not-found`*, *`permission`* | any |
| `database` | `database.engine` (the real chosen engine), `database.read-write`, *`constraints`*, *`transactions`* | integration, e2e, acceptance |
| `migrations` | `migrations.fresh`, *`migrations.upgrade`* | integration, e2e, acceptance |
| `contracts` | *`contracts.consumer`*, *`contracts.provider`* | contract |
| `queues` | `queues.contract`, *`acknowledgement`*, *`retry`*, *`duplicate`* | contract, integration; the others real suites |
| `ui` | `ui.smoke`, `ui.flows`; for a browser component also `ui.accessibility` and `ui.keyboard`, with a `boundary` per scenario ([decision 0018](0018-browser-builds-and-scenarios.md#requirements)) | e2e, acceptance |
| `authorization` | `authorization.forbidden`, *`authorization.isolation`* | any |

An optional requirement is either proved by a scenario or listed in
`[[not_applicable]]` with a reason; a mandatory one cannot be listed there.
Contract requirements are optional because a local mock proves no
compatibility with a third party: only an owned consumer or provider can be
verified. The catalog is the minimum. A manifest may add scenarios without
requirements for any other behavior its owners consider critical.

## Manifest

```toml
schema_version = 1
component = "notes"
openapi = "openapi.json"            # optional, relative to the component root

[[scenarios]]
id = "create-and-read"
summary = "A writer creates a note, then lists and fetches it."
capability = "http-api"
level = "integration"               # the suite whose results prove it
stage = "commit"                    # or "merge"
requirements = ["http-api.success"]
operations = ["listNotes", "createNote", "getNote"]
tests = ["integration.test_api::test_a_writer_creates_a_note_and_reads_it_back"]

[[not_applicable]]
requirement = "authorization.isolation"
reason = "All notes share one space; there are no tenants."

[[retired]]
id = "old-scenario"
reason = "Replaced by create-and-read."
```

`tests` are case names exactly as the suite's results report them:
`<module path>::<test>` for pytest, `<file>::<test>` for Vitest and
`package:test`, and `<class>::<method>` for PHPUnit, whose JUnit report
separates namespaces with dots, as in `App.Tests.SignTest::testSign`. A name ending in `*`
selects every case with that prefix and must select at least one.

The manifest is checked statically, without running tests, on every
`check` and by `quality scenarios`:

- `scenarios.manifest`: the file is missing, invalid TOML, larger than
  1 MiB, or names another component; or a component with capabilities names
  no manifest.
- `scenarios.reference`: an unknown requirement, a requirement of another
  capability, an undeclared capability or suite, an operation the OpenAPI
  document does not publish, or a `not_applicable` entry for an undeclared
  capability.
- `scenarios.uncovered`: a declared capability without a scenario, a
  requirement nobody proves, or an OpenAPI operation no scenario names.
- `scenarios.level`: a requirement proved by a suite it does not accept,
  such as a database requirement proved by a unit test.
- `scenarios.ambiguous`: a duplicate ID, test, or `not_applicable` entry, or
  a requirement both proved and excused.
- `scenarios.deleted`: a scenario of the base revision's manifest is gone
  without a `[[retired]]` entry, so removing an obligation is a visible,
  reviewed change.

## Proof

The suites a stage needs run as usual
([decision 0008](0008-test-and-coverage-engine.md)). Their results are then
matched with the scenarios required at that stage:

- every selected case must have passed. A failure or error is
  `scenarios.failed`; a skip or an expected failure is `scenarios.skipped`;
- a name that selects no case is `scenarios.absent`, with similar case names
  as hints. That covers renamed tests, a filter in the runner's settings, and
  an empty selection;
- an exact name that reports more than one case is `scenarios.ambiguous`;
- a suite that left no results, because it could not run or its evidence was
  invalid, is `scenarios.unverified`.

Each problem is reported once per scenario and kind. Text and JSON reports
add one line per component: how many scenarios were required and proved,
and how many were suspended or not required by this run.

### Stages

A scenario is mandatory at `commit`, the pre-commit hook and the default of
`quality check`, or at `merge`, `quality check --stage merge`. A merge
requires the commit scenarios too. A stage runs the unit suite and the
suites of the scenarios it requires, so a slow acceptance scenario can be
left to the merge gate. Without a manifest, only the unit suite runs.
`quality test` proves the scenarios of the suites it ran.

### What runners may not hide

Each profile closes the ways a test can look successful without running:

| Profile | Measures |
| --- | --- |
| Python | `xfail_strict=true`; the rerun plugins `rerunfailures` and `flaky` are disabled; `xfail` counts as an expected failure |
| TypeScript | `.only` fails the run; `test.fails` counts as an expected failure; a pass after a retry counts as a failure; errors outside tests fail the suite |
| Dart | `--no-retry`; a `solo` test is `tests.focused` and the skipped tests prove nothing |
| PHP | skipped and incomplete tests count as skipped; exit 2, for a test error, is excused like exit 1 only when quarantined tests alone failed |

A skip or an expected failure anywhere is `tests.skipped`, a warning; a
suite whose cases were all skipped is `tests.none`.

## Quarantine

A flaky test of an integration, contract, e2e, or acceptance suite can be
quarantined in `quality.toml`; unit tests cannot:

```toml
[[quarantine]]
component = "notes"
suite = "integration"
test = "integration.test_api::test_a_missing_note_is_not_found"
reason = "Times out on slow runners; tracked in #42."
owner = "notes maintainers"
expires = 2026-12-01
scenarios = ["missing-note"]
```

While the entry lasts, the test still runs and is reported
(`tests.quarantined`, a warning), its failure does not fail its suite, and it
never proves anything. The scenarios the entry names are suspended
(`scenarios.suspended`, a warning). A scenario that maps the test but is not
named fails with `scenarios.quarantined`, so a quarantine cannot silently
drop an obligation. An expired entry (`quarantine.expired`) and an entry
whose test is not among the results of its suite (`quarantine.unused`) fail
the check. Entries cannot be excepted.

## Suggestions

`quality scenarios` lists the scenarios and hints at capabilities a
component may have but does not declare (`scenarios.suggestion`, info). The
hints come from well-known dependencies in the package manifest, declared
executables, an OpenAPI document, and a `migrations` directory. They never
fail a check: dependencies can be unused, and only a component's owners know
its real boundaries and business requirements.

## Enhanced profiles

`enhanced` in a component lists optional profiles that its scenarios can be
tagged with:

| Profile | Readiness |
| --- | --- |
| `property` | Enforced: at least one scenario must be tagged; property tests may belong to the unit suite. |
| `performance` | Enforced: at least one tagged scenario, and not in the unit suite. |
| `mutation` | Not enforced: no profile runs a tested mutation tool yet, so enabling it is `scenarios.enhanced`, an error. |

No quota is imposed, and no tool is required beyond the profile's runner.

## Reference fixtures

Two small projects in `quality.testkit` exercise the rules in the acceptance
suite:

- `greet`, a Python library with a command line. Unit tests import it; the
  e2e suite builds a zipapp and runs it.
- `notes`, an HTTP API on the standard library with SQLite and numbered
  migrations. Its OpenAPI document publishes three operations, and only
  writers may create notes.

Both pass the gate, and the acceptance tests break them in every way above.
They are test fixtures, not a framework a project must use.

## This repository

The core and the launcher declare `cli`. Their e2e suites copy the candidate
without installed environments, as a fresh clone of the template, and run
`./quality` through the root symlink. The core's acceptance suite proves its
commit gate at the merge stage. The helpers declare no boundary: only their
profile adapter runs them, and the core's acceptance suites drive them.
