# 0003 Architecture and configuration layout

Status: accepted (P00); launcher path amended in P05, adapter layout in P06,
CI wrappers and reports in P10, the template layer and dormant helpers in P11.

## Repository layout

| Path | Role |
| --- | --- |
| `quality` | Symlink to the stdlib launcher `tools/quality/launcher/src/quality_launcher.py` ([decision 0010](0010-launcher-bootstrap-and-hooks.md)) |
| `quality.toml` | The single policy source: thresholds, components, classification, exceptions |
| `tools/quality/` | The always-required Python tooling core (`pyproject.toml`, `uv.lock`) |
| `tools/quality/src/quality/` | Core package |
| `tools/quality/tests/unit/` | Core unit suite (shipped with the template) |
| `tools/quality/tests/acceptance/` | Maintainer acceptance harness (left out of an export, [decision 0016](0016-template-lifecycle.md)) |
| `tools/quality/launcher/` | The launcher component: stdlib source, tests, and its own lockfile |
| `tools/quality/helpers/{typescript,dart,php}/` | Native helper bundles, each a native project with its own lockfile and tests |
| `tools/quality/toolchains.toml` | Toolchain pins and checksums |
| `docs/` | Documentation and decisions; the plan records are left out of an export |
| `.github/workflows/`, `ci/gitlab/` | Thin CI wrappers ([decision 0015](0015-ci-wrappers-and-merge-gates.md)) |
| `.github/CODEOWNERS`, `.github/rulesets/` | Owners of the control files and the prepared branch ruleset |

The core does not own the repository root: application components may use the
root `pyproject.toml`, `package.json`, `pubspec.yaml`, or `composer.json`.

## Core boundaries

Each subpackage has one responsibility. Dependencies point downwards only and
are enforced on the core by the declared-module architecture rule:

1. `diagnostics` – diagnostic model, rule registry, rendering, exit codes.
   `strictjson` below it parses JSON with a nesting bound that holds on every
   machine, because the standard decoder's limit follows the C stack.
2. `process` – subprocess execution with argument arrays, explicit working
   directory, sanitized environment, timeouts, bounded output, and process
   group cleanup on timeout or interruption.
3. `policy` – pydantic models (`extra="forbid"`), loading, cross-validation,
   JSON Schema export.
4. `gitio` – Git plumbing, candidate identity, snapshot materialization, diff
   and revision mapping.
5. `discovery` – file classification, component ownership, role activation.
6. `metrics`, `coverage`, `testing` – normalized models and native report
   parsers; no subprocess calls. `reports` sits above `coverage` and
   `testing` and exports their results as JUnit, LCOV, and Cobertura.
7. `adapters` – one package per language profile, plus modules the profiles
   share (configuration readers and static-check targets); they call native
   tools through `process` and return normalized results. The profile packages
   do not import each other.
8. `rules` – common repository, documentation, secret, blob, exception,
   structure, and architecture rules over normalized data.
9. `template`, `ci`, `hooks`, `toolchain`, `scenarios` – feature modules;
   `ci` holds the workflow, CODEOWNERS, control-file, and job-result rules,
   and `template` the project export
   ([decision 0016](0016-template-lifecycle.md)).
10. `engine` and `cli` – orchestration and argument parsing only.

Lower layers never import higher layers; cycles are errors. The exact order
is the `layers` list under `[components.architecture]` in `quality.toml`.

## Language adapters

An adapter implements one protocol: inventory (which files it owns), format
check, lint, type check, structure metrics and imports (via the core Python
`ast` module or the native helper), unit/integration test execution with
coverage, safe fixes, and doctor probes. Adapters reuse native tools; the core
never parses a language itself except Python via the standard `ast` module.

## Roles and activation

Every component is declared in `quality.toml` with `role`:

- `application` – the project's own code; activates its profile toolchain.
- `tooling` – the Python core; always active.
- `helper` – a native helper bundle under `tools/quality/helpers`. It is
  active when an application component uses its profile, when the candidate
  changes any of its files, or when `repository.mode = "maintainer"` (the
  template's own repository). Otherwise it is dormant: bootstrap does not set
  it up, so its profile toolchain is never downloaded, and checks skip it.

Discovery reports manifests outside declared components and source files
outside declared roots as errors, so helper manifests and fixtures never
activate an unrelated profile by accident.

## Policy layout

`quality.toml` holds `schema_version`, `[repository]`, `[thresholds]`,
`[coverage]`, `[[components]]` (root, profile, role, source and test roots,
capabilities, modules, scenarios file), `[[classification]]`, and
`[[exceptions]]`. Native tool configurations that need policy values (Pyright
strictness, coverage source lists) are generated into the snapshot at run time
or checked for consistency; thresholds are never duplicated in native configs.

## Diagnostics and exit codes

Every finding has a stable rule ID, severity, repository-relative location,
message, and corrective hint; `quality explain <rule-id>` prints the
requirement. Exit codes: `0` pass, `1` gate violations, `2` usage or policy
error, `3` environment or tool error, `4` internal error.

## Non-goals

No package manager, build system, general-purpose parser, test framework,
result cache, or hosted service is implemented.
