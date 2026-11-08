# Command-line reference

Run every command from the repository root as `./quality <command>`.
`./quality <command> --help` prints the full option list. Commands that judge
a candidate read the index by default, or a commit with `--rev`, from an
isolated snapshot ([decision 0004](../decisions/0004-candidate-snapshots.md)).
`check`, `test`, `self-test`, `gate`, and `audit` are judged by the candidate's
own core: when
the working-tree core differs, the candidate's core runs the command as a
child process, and a candidate core lockfile that differs from the installed
environment fails with `core.environment`.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Passed |
| 1 | Violations found |
| 2 | Usage or policy error |
| 3 | Environment or tool error |
| 4 | Internal error |

## Setup and environment

| Command | Purpose |
| --- | --- |
| `bootstrap [--pre-push]` | Install the pinned tools, interpreters, and locked packages, then the managed hooks ([decision 0010](../decisions/0010-launcher-bootstrap-and-hooks.md)) |
| `doctor [--json]` | Verify offline and read-only that checks can run here |
| `tools install [NAME ...]` | Download, verify, and install pinned tools from `tools/quality/toolchains.toml`: gitleaks, actionlint, and osv-scanner by default, or the named ones such as `node`, `dart`, `composer`, or `chromium`; bootstrap installs Node only for a TypeScript component, Chromium only for a browser component, the Dart SDK only for a Dart component, and Composer only for a PHP component |
| `tools update NAME VERSION` | Rewrite one tool's pins in `toolchains.toml` for another release, with checksums from the vendor's checksum file that its `checksums` template names; nothing is installed or staged ([maintenance guide](../guides/maintenance.md)) |

## Project lifecycle

[Decision 0016](../decisions/0016-template-lifecycle.md) describes these commands.

| Command | Purpose |
| --- | --- |
| `template export DIRECTORY [--rev REV]` | Write a commit of this repository, without `[template].exclude` and the maintainer-only regions of `[template].fenced`, to a new or empty directory, and make it a repository whose root commit, made with your Git identity, carries a `Quality-Template` trailer naming that commit; the launcher may download the core environment for it |
| `component add ROOT [--id ID] [--profile P] [--target T] [--source DIR ...] [--unit DIR ...] [--e2e DIR ...] [--scenarios FILE] [--capability C ...] [--no-boundary-reason TEXT]` | Append an application component to `quality.toml`, with the profile detected from its manifest and the profile's conventional source and unit test directories, and for a browser application its `e2e` directory and `scenarios.toml` when they exist; the edit is validated before it is written, and nothing is staged. Python components receive a missing `.python-version` from the core's interpreter pin; existing files are preserved. `--target node` or `browser` says where a TypeScript component runs; React or Vite evidence without it is refused as ambiguous, and `browser` requires a complete React + Vite application ([decision 0017](../decisions/0017-browser-frontend-profile.md#registration)) |

## Checking and testing

| Command | Purpose |
| --- | --- |
| `check [--staged \| --rev REV] [--all] [--stage commit\|merge] [--json]` | Every gate over the affected components, or every active one with `--all`, and the suites that prove the scenarios mandatory at the stage ([decision 0014](../decisions/0014-behavioral-scenarios.md)); the pre-commit hook runs `check --staged` at the commit stage |
| `test [--rev REV] [--suite S] [--component C] [--all] [--json]` | The declared suites, and the scenarios they prove; unit suites enforce the coverage floors |
| `self-test [--level unit\|e2e\|acceptance] [--rev REV] [--json]` | The suites of the tooling components; `e2e` runs the built-artifact suites |
| `fix [--json]` | Safe native fixes and formatting of the active components in the working tree; nothing is staged |
| `links [--rev REV] [--timeout S] [--json]` | Probe external documentation links; not part of `check` |

## Merge CI

CI wrappers translate their provider's events into these commands; the
commands read no provider variables
([decision 0015](../decisions/0015-ci-wrappers-and-merge-gates.md)).

| Command | Purpose |
| --- | --- |
| `gate --stage commit\|merge [--base REV --head REV] [--tested REV \| --queue] [--lane main\|browser] [--index FILE] [--reports DIR] [--baseline FILE] [--json]` | Every gate over every active component of one candidate: the index, a pushed range, a pull request's tested merge, or a merge-queue entry; a range also checks every blob of its new commits. `--lane browser` checks only the browser components and `--lane main` everything else, so two CI jobs together run the whole gate. `--reports` writes `report.json`, JUnit, LCOV, Cobertura, and the native reports; `--baseline` reports coverage regressions against an earlier `report.json` as warnings |
| `audit [--rev REV] [--offline-db DIR] [--json]` | Match every lockfile against the OSV advisories with the pinned osv-scanner, from the osv.dev API or an offline database; `[audit]` in the policy sets the blocking CVSS score |
| `ci verdict --needs FILE [--rev REV] [--json]` | The aggregate job's decision: fail unless every job in `[ci].required_jobs` succeeded, from results in the form of GitHub's `toJSON(needs)` |

## Inspection and policy

| Command | Purpose |
| --- | --- |
| `explain RULE \| --list` | What a rule checks and how to fix it |
| `inventory [--rev REV] [--json]` | How each candidate file is classified, with discovery findings |
| `policy validate` | Validate [`quality.toml`](../../quality.toml) |
| `policy schema [--scenarios] [--write \| --check]` | Print the generated [policy schema](policy.schema.json), or the [scenario manifest schema](scenarios.schema.json) with `--scenarios`; write or check both |
| `scenarios [--rev REV] [--json]` | Validate the scenario manifests without running tests, list their scenarios, and suggest undeclared capabilities |

## JSON reports

With `--json`, a command prints one JSON document with `schema_version`,
`command`, `exit_code`, a `summary` of error and warning counts, and the
`diagnostics`. Each finding carries its rule ID, severity, message, location,
component, and hint. `check`, `test`, and `self-test` add the tested
components, their coverage counters, and per component the scenarios required,
proved, suspended, and not required by the run; `gate` adds the candidate's
revisions and trees; `audit` adds the scanned lockfiles and how many findings
exceptions cover; `scenarios` adds every scenario of the manifests; `doctor`
adds its check lines.
