# Test quality

This guide says what the gate accepts as test evidence and how to write
tests that keep proving what they claim. The rules behind it are in
[decision 0008](../decisions/0008-test-and-coverage-engine.md) (suites and
coverage) and [decision 0014](../decisions/0014-behavioral-scenarios.md)
(capabilities and scenarios). The thresholds are in
[`quality.toml`](../../quality.toml).

## What counts as evidence

Only the results of the run that judges the candidate count. The suites run
in an isolated snapshot of the staged tree or the revision, and their
reports are written to a new empty directory. A report from an earlier run,
an unstaged test, or a test the runner never executed proves nothing.

- Every component has a unit suite that must execute at least one test and
  meet the coverage floors, including the changed-line floor.
- Each scenario of a component's manifest names test cases, and every one
  of them must have run and passed in this run.
- A skip, an expected failure (`xfail`, `test.fails`), a focus marker
  (`solo`, `.only`), a pass after a retry, a deselected test, and a
  quarantined test never prove anything.

## Choosing the level

| Suite | Use it for | Notes |
| --- | --- | --- |
| unit | Logic, through the component's public API | Loopback only, where the system allows it; coverage is measured here only |
| integration | The real boundary the component owns: its database engine, migrations, an HTTP server | Database and migration requirements need at least this level |
| contract | Owned consumer and provider expectations | A mock of a third party proves no compatibility |
| e2e | The built artifact as a user runs it | CLI and UI smoke tests belong here |
| acceptance | Whole workflows, such as commits through real Git hooks | A slow scenario can be required at the merge stage only |

Prefer the lowest level that proves the behavior, and prove each boundary
once at a level that actually crosses it. A scenario can map several tests,
and a test can serve several scenarios.

## Writing tests that stay meaningful

- Name a test after the behavior it shows. The manifest maps case names, so
  renaming a test is a manifest change, and the gate reports the old name as
  absent with similar names as hints.
- Assert observable results: return values, output, exit statuses, HTTP
  statuses and bodies, rows in the database. A test that only runs code
  raises coverage without proving behavior.
- Exercise the boundary for real at the levels above unit: start the server,
  open the database file, build and run the artifact.
- Keep each test independent of the others and of their order, so that a
  failure points at one behavior.
- Do not edit runner settings to hide tests. Filters, deselections, and
  retry options in a component's configuration surface as absent or failed
  scenarios.

## Flaky tests

Fix a flaky test first. When it cannot be fixed at once, quarantine it in
`quality.toml` with a reason, an owner, an expiry date, and the scenarios it
suspends, as [decision 0014](../decisions/0014-behavioral-scenarios.md#quarantine)
describes. Unit tests cannot be quarantined. Every run reports a quarantined
test, and the entry fails the check once it expires or its test is gone.

## Capabilities and requirements

Declare every boundary of a component as a capability, or give
`no_boundary_reason` when it has none. `./quality scenarios` validates the
manifests without running tests and suggests capabilities that the
component's dependencies, executables, OpenAPI document, or migrations hint
at. The suggestions are hints, not requirements: only the component's owners
know its real boundaries and its critical business flows, and those flows
belong in the manifest as scenarios of their own.

## Enhanced profiles

Property and performance testing can be enabled per component and are then
enforced through tagged scenarios. Property tests may live in the unit
suite; performance scenarios may not. Mutation testing is not enforced yet,
because no profile runs a tested mutation tool, so enabling it fails the
check rather than claiming a guarantee it cannot give. See
[decision 0014](../decisions/0014-behavioral-scenarios.md#enhanced-profiles).
