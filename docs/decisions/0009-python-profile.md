# 0009 Python profile

Status: accepted (P05); amended in P11 and P12.

## Scope

The Python profile checks a component with the pinned Ruff for formatting and
lint, the pinned Pyright in strict mode for types, and pytest with coverage.py
for its suites. The structure metrics are the common ones of
[decision 0007](0007-common-gates.md); the test and coverage engine is
[decision 0008](0008-test-and-coverage-engine.md). Ruff and Pyright are
locked in the core's own `uv.lock` and run from the core environment. pytest,
pytest-cov, and coverage.py come from the component's lockfile, because they
import the code under test.

## Component contract

`./quality component add ROOT` creates `.python-version` from the core's pinned
interpreter when it is absent and preserves an existing file. Stage it with
the component before running `./quality bootstrap` and making the first commit,
as shown in the [getting started commands](../../README.md#getting-started).

Before any tool runs, the profile reads the component's native configuration
from the candidate snapshot. A component that fails this check is not
linted, typed, or tested; its findings say what to fix. Every finding is
`python.config`. An exception to it needs the usual reason, owner, and expiry
or review date ([decision 0007](0007-common-gates.md)).

| File | Requirement |
| --- | --- |
| `pyproject.toml` | Has a `[project]` table, so uv can sync the component |
| `uv.lock` | Exists, locks `coverage`, `pytest`, and `pytest-cov`, and takes no package from outside the repository (no absolute, home, or parent paths, no `file:` URLs) |
| `.python-version` | One exact version such as `3.14.8` |
| Ruff settings | `.ruff.toml`, `ruff.toml`, or `[tool.ruff]`; an explicit `lint.select`; no `ALL` in `ignore`, `extend-ignore`, or per-file ignores |
| Pyright settings | `pyrightconfig.json` or `[tool.pyright]` with `typeCheckingMode = "strict"`; no strict switch turned off; no rule lowered to `none`, `information`, or `warning`; no `ignore` or `defineConstant`; paths inside the repository; no tracked `venv` |
| pytest settings | `pytest.ini`, `.pytest.ini`, or `[tool.pytest]` / `[tool.pytest.ini_options]`, in that precedence; every `pythonpath` entry inside the repository |

Ruff `extend` and Pyright `extends` chains are followed, at most five files
deep, and must stay inside the repository. Coverage settings
(`[tool.coverage]`, `.coveragerc`) are never read: the runner writes its own.

## Environments

Every check builds a new environment for each component it covers, inside the
snapshot's work area: the pinned uv runs `uv sync --frozen --offline
--compile-bytecode` from the candidate's `uv.lock` with the interpreter of
`.python-version`. The suites never write bytecode, so without the compiled
packages every Python process of a suite would compile pytest and coverage.py
again; the candidate's own source stays uncompiled. Checks never
download. A package or interpreter missing from uv's cache is an environment
error that names `./quality bootstrap`, which fills the cache
([decision 0010](0010-launcher-bootstrap-and-hooks.md)).

## Static checks

The gate passes explicit file lists: the component's source and test files
ending in `.py` or `.pyi`.

| Rule | Command |
| --- | --- |
| `python.format` | `ruff format --check --no-force-exclude --no-cache` |
| `python.lint` | `ruff check --no-fix --no-force-exclude --no-cache` |
| `python.types` | `pyright --outputjson --project <settings> --pythonpath <component interpreter>` |

Exclusions in the component's configuration do not apply to the files the
gate names, and a file Pyright leaves out of analysis fails. Each tool's exit
status must agree with its report; a disagreement or an unreadable report is
`tools.failed`. A finding of a component is matched to exceptions only when
every component of the profile was checked, so an exception is never reported
unused because its component was skipped.

## Suites

pytest runs from the component environment with `-c <settings>`,
`--rootdir=<component>`, no cache provider, and a JUnit report in the run's
output directory. Every run sets `xfail_strict=true`, so an unexpected pass
of an expected failure fails, and disables the rerun plugins
`rerunfailures` and `flaky`; an expected failure counts as skipped
([decision 0014](0014-behavioral-scenarios.md)). Unit runs add `--cov` for each source root, `--cov-branch`,
and a coverage configuration written by the runner (branches on, data file in
the output directory). The JSON coverage report is read under the rules of
decision 0008.

The component's own pytest settings still apply, so a component whose
lockfile locks pytest-xdist may run its suites on parallel workers;
pytest-cov combines the workers' coverage before the report is written. The
core runs its own suites on one worker per CPU: most of their time goes to
the Git commands, tools, and nested gates they start, which one test at a
time leaves mostly idle. A worker may run any test, so no test depends on
another, and none carries an `xdist_group` mark, which would rename it in
the JUnit report that scenarios are matched against.

Each run gets a new home, cache, and temporary directory in the work area.
Only the pinned toolchains and uv's package cache are linked in, so tests
that call offline tools keep working. The inherited environment is the
allowlist of the process layer.

## Fixes

`./quality fix` runs `ruff check --fix --no-unsafe-fixes` and then
`ruff format` on the component's working-tree source and test files, with the
same configuration as the check. It never stages anything. Findings that
remain afterwards are reported exactly as the check reports them.

## Residual risks

- Unit tests run with the user's permissions. They reach loopback only
  ([decision 0008](0008-test-and-coverage-engine.md#network-isolation)) unless
  the system forbids unprivileged user namespaces; the check then warns with
  `tests.network`, and isolation is a review and CI concern.
- A test can import from the component environment anything its lockfile
  locks; the lockfile itself is reviewed code.
- Pyright's strict mode is the floor. Rules beyond strict mode are the
  component's choice.
