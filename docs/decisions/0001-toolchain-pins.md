# 0001 Toolchain pins

Status: accepted (P00, 2026-10-20). Versions were read from the primary
registries on that date; checksums were verified against vendor checksum files.
Since P03 the machine-readable source of truth for downloaded standalone tools
is `tools/quality/toolchains.toml` (uv, gitleaks and its rule set, osv-scanner,
actionlint); profile runtimes join it with their phases. Since P06 it also
pins Node.js and, under `[packages.npm]`, the npm packages every TypeScript
component must lock ([decision 0011](0011-typescript-profile.md)). Since P07
it pins the Dart SDK and, under `[packages.pub]`, the pub packages every Dart
component must lock ([decision 0012](0012-dart-profile.md)). Since P08 it
pins Composer, under `[packages.composer]` the Composer packages every PHP
component must lock, and under `[runtimes]` the minor versions of PHP and
Xdebug, which the PHP profile requires but does not install
([decision 0013](0013-php-profile.md)). Since P11 each tool names its
vendor's checksum file in `checksums`, from which `./quality tools update`
derives a new release's pins, and the gitleaks rule set is fetched at the
release tag instead of a commit, so its URL follows the version
([decision 0016](0016-template-lifecycle.md#updating-pinned-tools)). This
record explains the choices.

## Shared core

| Tool | Version | Source | Notes |
| --- | --- | --- | --- |
| uv | 0.12.24 | github.com/astral-sh/uv/releases | 0.13.x had four releases in eleven days; the last 0.12 patch is the stable choice |
| CPython (core) | 3.14.8 | python.org, uv managed builds | 3.15.0 is eleven days old; 3.14 is supported by every selected tool |
| pydantic | 2.14.1 | pypi.org/project/pydantic | Typed policy models and JSON Schema export |
| markdown-it-py | 4.2.0 | pypi.org/project/markdown-it-py | CommonMark tokens for links, anchors, prose counts |
| pymarkdownlnt | 0.9.41 | pypi.org/project/pymarkdownlnt | Markdown structure linting in Python |
| defusedxml | 0.7.1 | pypi.org/project/defusedxml | XML reports without entity expansion |
| PyYAML | 6.0.3 | pypi.org/project/PyYAML | pubspec, OpenAPI, and workflow parsing (safe loader only) |
| gitleaks | 8.30.1 | github.com/gitleaks/gitleaks/releases | Secret scanning of directories and Git ranges |
| osv-scanner | 2.7.0 | github.com/google/osv-scanner/releases | Lockfile vulnerability audit for all four ecosystems |
| actionlint | 1.7.12 | github.com/rhysd/actionlint/releases | GitHub workflow validation |

## Python profile

| Tool | Version | Notes |
| --- | --- | --- |
| Ruff | 0.17.1 | Formatter and linter with an explicit rule selection |
| Pyright | 1.1.414 | The PyPI wheel bundles the JavaScript and uses `nodejs-wheel-binaries`; no download at run time (verified) |
| pytest | 9.1.1 | Unit and integration runner, JUnit XML |
| coverage.py | 7.16.2 | `--branch`, JSON report format 3 with per-arc data |
| pytest-cov | 7.1.0 | Thin integration with coverage.py |
| pytest-xdist | 3.8.0 | Runs the core's own suites on one worker per CPU (added in P11) |

## TypeScript profile

| Tool | Version | Notes |
| --- | --- | --- |
| Node.js | 24.22.0 (LTS "Krypton") | Official tarball with `SHASUMS256.txt` |
| npm | bundled with Node 24.22.0 | `npm ci --offline` from `package-lock.json` |
| TypeScript | 6.0.3 | TypeScript 7.0.2 is the native port; typescript-eslint 8.72.0 requires `>=4.8.4 <6.1.0`, so 6.0.3 is the newest compatible release |
| ESLint | 10.13.0 with `@eslint/js` 10.0.1 | Flat config |
| typescript-eslint | 8.72.0 | Typed linting through the project service |
| Prettier | 3.9.10 | Single formatter for TypeScript files |
| Vitest | 5.0.5 with `@vitest/coverage-v8` 5.0.5 | V8 coverage remapped to original sources |

## Dart profile

| Tool | Version | Notes |
| --- | --- | --- |
| Dart SDK | 3.13.6 stable | Official archive with `.sha256sum` |
| package:test | 1.33.0 | JSON file reporter |
| package:coverage | 1.16.0 | Formats the VM's raw branch coverage as LCOV |
| package:lints | 6.1.0 | Official recommended set |
| package:analyzer | 14.5.0 | Structural metrics helper, locked in the helper's own `pubspec.lock` |

## PHP profile

| Tool | Version | Notes |
| --- | --- | --- |
| PHP | 8.4.x (system) | Distribution or ondrej PPA packages; minor version pinned, see 0002 |
| Xdebug | 3.5.x (system) | The only accepted coverage driver; in coverage mode for unit runs only |
| Composer | 2.10.3 | `composer.phar` with published SHA-256, run by the system PHP |
| PHPUnit | 13.4.3 | Requires PHP >= 8.4.1 |
| php-code-coverage | 14.4.1 | Writes the Clover and JSONL reports the gate parses, so its version is pinned with PHPUnit's |
| PHPStan | 2.3.1 | Highest level (`max`) |
| PHP CS Fixer | 3.95.27 | Single formatter for PHP files |
| nikic/php-parser | 5.9.0 | Structural metrics helper, locked in the helper's own `composer.lock` |

## Checksums (Linux)

| Artifact | SHA-256 |
| --- | --- |
| uv x86_64 | `b4dfaef47d491a7296981f8374a4595f55dbf84e8937c8ecd2983574d8bb3da6` |
| uv aarch64 | `5231be65f496304623895dacdbf1de8504fec90303684bdf05805aa34414dd21` |
| Node x64 | `cc2f601cee82d9a5a233302fe307abc600727035fd7d2c3a3571755989e95008` |
| Node arm64 | `fef00bb4d36e1c50104a9775a10359beee98f162b4104dd12472c75065c6a143` |
| Dart x64 | `f80780c01ade17b789cf4dc2425d662f63bdd40eceda53b36c0d46af650de41b` |
| Dart arm64 | `73753a2e009505a2ba0d0c0bd449e248001a01984c805a3fcc19671dbb9616b1` |
| Composer phar | `7a2d379d5b8ffdaa028580ef26494c36d2feef4b178d3dd1473a4dbc5e17c8d6` |
| gitleaks x64 | `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb` |
| gitleaks arm64 | `e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080` |
| gitleaks rules (`config/gitleaks.toml` at tag `v8.30.1`, commit `83d9cd6`) | `e163e53b9e7e8a8511e77271e2b323ed057759542a6d988258afe3a1fa329caf` |
| osv-scanner amd64 | `160c6253e2922e9e0a6a9771c5c4c40a4b3c6090b34a3cdd5e80c96cc2e4218b` |
| osv-scanner arm64 | `4061ef4c021ed3c47f203b818f337e351336cc5cd67c0274fc36bd1dfc728dd7` |
| actionlint amd64 | `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8` |
| actionlint arm64 | `325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6` |

## Primary references

- Git hooks and index export: <https://git-scm.com/docs/githooks>,
  <https://git-scm.com/docs/git-checkout-index>
- coverage.py source and branch measurement:
  <https://coverage.readthedocs.io/en/latest/source.html>,
  <https://coverage.readthedocs.io/en/latest/branch.html>
- Ruff rules: <https://docs.astral.sh/ruff/linter/>
- typescript-eslint typed linting:
  <https://typescript-eslint.io/getting-started/typed-linting/>
- Vitest coverage: <https://vitest.dev/guide/coverage>
- Dart analysis and coverage: <https://dart.dev/tools/analysis>,
  <https://pub.dev/packages/coverage>, <https://pub.dev/packages/analyzer>
- PHPStan: <https://phpstan.org/user-guide/getting-started>
- PHPUnit 13 manual: <https://docs.phpunit.de/en/13.4/>
- Pact applicability: <https://docs.pact.io/getting_started/what_is_pact_good_for>
