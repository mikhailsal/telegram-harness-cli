# Quality core

The `quality` command-line tool decides whether a candidate change passes.
Git hooks and CI only call it. This directory holds its typed Python source,
its tests, the stdlib launcher, and locked development tooling.

## Layout

| Path | Contents |
| --- | --- |
| `src/quality/diagnostics` | Finding model, rule registry, text and JSON renderers |
| `src/quality/process` | Subprocess runner and allowlisted child environments |
| `src/quality/policy` | `quality.toml` models, loading, cross-validation, schema export, and the text edit of `component add` |
| `src/quality/gitio` | Candidate trees, isolated snapshots, and changed-line diffs from Git plumbing |
| `src/quality/discovery` | File types, manifests, classification, ownership, activation, test scope |
| `src/quality/metrics` | Structure metrics model shared by profile adapters |
| `src/quality/boundedio.py` | Bounded JSON and XML readers for native tool reports |
| `src/quality/testing` | Suite and result model, JUnit XML and Dart JSON reporter parsers (PHPUnit writes JUnit XML) |
| `src/quality/reports` | The `--reports` directory of `quality gate`: JUnit, LCOV, and Cobertura exports and native report copies |
| `src/quality/coverage` | Coverage model and parsers for coverage.py JSON, LCOV, and PHPUnit's Clover and JSONL reports |
| `src/quality/adapters` | Native tool adapters: the Python profile (Ruff, Pyright, pytest, uv environments, fixes), the TypeScript profile (Prettier, ESLint, tsc, Vitest, npm environments, fixes, and the browser target's Stylelint, import boundaries, entry points, production build and its output, Chromium, the server of the build, and the Playwright scenarios with their traces and accessibility scans), the Dart profile (dart format, dart analyze, package:test, package:coverage, pub environments, fixes), the PHP profile (Composer, PHP CS Fixer, PHPStan, PHPUnit with Xdebug, the system PHP, fixes), Python structure and imports, Markdown lint, gitleaks, actionlint, osv-scanner, HTTP probes |
| `src/quality/rules` | Common rules: files, configuration, suppressions, docs, links, structure, architecture, exceptions, tests, quarantine, coverage |
| `src/quality/scenarios` | Capability catalog, scenario manifests, static validation, proof from test results, and capability suggestions |
| `src/quality/toolchain` | Pinned standalone tool manifest, verified installation, and `tools update` from vendor checksum files |
| `src/quality/hooks` | Text, installation, and status of the managed Git hooks |
| `src/quality/ci` | CI wrapper rules: workflow trust, the aggregate job, the lanes of the gate, CODEOWNERS, control files, and the job-result verdict |
| `src/quality/template` | Template export: maintainer-only fences, left-out paths, and the new project's root commit |
| `src/quality/engine` | Orchestration of `check`, `gate`, `audit`, history ranges, external links, test suites, fixes, bootstrap, and doctor |
| `src/quality/catalog.py` | The registry of every rule the core can report |
| `src/quality/cli` | Argument parsing and subcommands (`audit`, `bootstrap`, `check`, `ci`, `component`, `doctor`, `explain`, `fix`, `gate`, `inventory`, `links`, `policy`, `scenarios`, `self-test`, `template`, `test`, `tools`) |
| `src/quality/testkit` | Test builders shared by unit, contract, and acceptance tests, including scratch projects with real hooks, a React + Vite browser component locked from the seed in `browserseed/`, the `greet` and `notes` scenario fixtures, and offline advisory databases |
| `toolchains.toml` | Versions, URLs, and SHA-256 checksums of standalone tools, Node, the Dart SDK, and Composer; the npm, pub, and Composer packages TypeScript, Dart, and PHP components lock; and the PHP and Xdebug minor versions the system must provide |
| `helpers/typescript` | The Node helper of the TypeScript profile: types, structure, imports, and ESLint configuration probes; a separate component |
| `helpers/dart` | The Dart helper of the Dart profile: structure and imports with package:analyzer; a separate component |
| `helpers/php` | The PHP helper of the PHP profile: structure, imports, and which files only declare, with nikic/php-parser; a separate component |
| `tests/unit` | Unit tests, including the contract every language profile meets; they must keep core coverage at the policy minimum |
| `tests/e2e` | End-to-end tests: a project exported from a fresh clone, without installed environments, run through `./quality` |
| `launcher/` | The `./quality` launcher, a separate component that runs on the system `python3`; `launcher/e2e` runs it from a fresh copy |

Packages depend only on lower layers, as described in
[decision 0003](../../docs/decisions/0003-architecture.md); the layer order is
declared in the root `quality.toml` and enforced by `quality check`. The gates
themselves are described in
[decision 0007](../../docs/decisions/0007-common-gates.md), and the test and
coverage engine in
[decision 0008](../../docs/decisions/0008-test-and-coverage-engine.md). The Python profile is
[decision 0009](../../docs/decisions/0009-python-profile.md), the TypeScript profile
[decision 0011](../../docs/decisions/0011-typescript-profile.md), the Dart profile
[decision 0012](../../docs/decisions/0012-dart-profile.md), the PHP profile
[decision 0013](../../docs/decisions/0013-php-profile.md), behavioral scenarios
[decision 0014](../../docs/decisions/0014-behavioral-scenarios.md), the CI wrappers and merge gates
[decision 0015](../../docs/decisions/0015-ci-wrappers-and-merge-gates.md), the template
lifecycle [decision 0016](../../docs/decisions/0016-template-lifecycle.md), and the launcher,
bootstrap, doctor, and hooks are
[decision 0010](../../docs/decisions/0010-launcher-bootstrap-and-hooks.md).

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Passed |
| 1 | Violations found |
| 2 | Usage or policy error |
| 3 | Environment or tool error |
| 4 | Internal error |

## Native development commands

Day to day, use the gate itself from the repository root:
`./quality self-test` runs the unit suites with coverage. The tool versions
are locked in `pyproject.toml` and `uv.lock`
([decision 0001](../../docs/decisions/0001-toolchain-pins.md)); after
`./quality bootstrap`, the native commands run from this directory:

```sh
.venv/bin/ruff format --check src tests
.venv/bin/ruff check src tests
.venv/bin/pyright
PYTHONPATH=src .venv/bin/python -m pytest tests/unit --cov=src --cov-branch
```

pytest runs the core's suites on one pytest-xdist worker per CPU, as set in
`pyproject.toml`; add `-n 0` to run them in one process, for example under a
debugger. Any worker may run any test, so each test sets up its own state;
a module fixture may run once per worker.

The TypeScript helper runs on the pinned Node. `./quality bootstrap` caches
the packages of its `package-lock.json`, and `npm ci --offline` installs them
into `node_modules`. The native commands run from `helpers/typescript` with
that Node first on `PATH` (`~/.cache/quality/toolchains/node-24.22.0/bin` by
default):

```sh
npm ci --offline --ignore-scripts
node_modules/.bin/prettier --check src tests eslint.config.js vitest.config.ts
node_modules/.bin/eslint src tests
node_modules/.bin/tsc -p .
node_modules/.bin/vitest run --coverage --coverage.include=src --coverage.reporter=text-summary
```

The Dart helper runs on the pinned SDK. `./quality bootstrap` caches the
packages of its `pubspec.lock` in `~/.cache/quality/pub` and compiles it. The
native commands run from `helpers/dart` with
`~/.cache/quality/toolchains/dart-3.13.6/bin` first on `PATH` and
`PUB_CACHE=~/.cache/quality/pub`:

```sh
dart pub get --offline --enforce-lockfile
dart format --output=none --set-exit-if-changed bin lib test
dart analyze --fatal-infos
dart test --branch-coverage --coverage=.dart_tool/coverage
dart run coverage:format_coverage --lcov --in=.dart_tool/coverage \
  --out=.dart_tool/lcov.info --report-on=bin --report-on=lib
```

The PHP helper runs on the system PHP with Xdebug
([decision 0013](../../docs/decisions/0013-php-profile.md#runtime)).
`./quality bootstrap` installs the pinned Composer and caches the packages of
the helper's `composer.lock` in `~/.cache/quality/composer`. The native
commands run from `helpers/php` with
`COMPOSER_CACHE_DIR=~/.cache/quality/composer`; PHP CS Fixer takes one path
per run when no configuration file is given:

```sh
COMPOSER_DISABLE_NETWORK=1 php ~/.cache/quality/toolchains/composer-2.10.3/composer \
  install --no-scripts --no-plugins --no-progress
for path in bin src tests; do
  php vendor/bin/php-cs-fixer check --rules=@PER-CS --using-cache=no "$path"
done
php -d memory_limit=1G vendor/bin/phpstan analyse --level=max --no-progress bin src tests
XDEBUG_MODE=coverage php -d zend_extension=xdebug vendor/bin/phpunit \
  --bootstrap vendor/autoload.php --coverage-filter bin --coverage-filter src \
  --branch-coverage --disable-coverage-ignore --coverage-text tests
```

After changing a policy or scenario manifest model, regenerate both committed
schemas from the repository root:

```sh
./quality policy schema --write
```
