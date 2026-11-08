# Documentation index

## Decisions

| Decision                                               | Topic                                                                                        |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| [0001](decisions/0001-toolchain-pins.md)               | Toolchain pins and versions                                                                  |
| [0002](decisions/0002-installation-strategy.md)        | Installation strategy and prerequisites                                                      |
| [0003](decisions/0003-architecture.md)                 | Architecture and configuration layout                                                        |
| [0004](decisions/0004-candidate-snapshots.md)          | Candidate snapshots and revision semantics                                                   |
| [0005](decisions/0005-coverage-measurement.md)         | Coverage measurement                                                                         |
| [0006](decisions/0006-discovery-and-classification.md) | Discovery and classification                                                                 |
| [0007](decisions/0007-common-gates.md)                 | Common repository, documentation, and structure gates                                        |
| [0008](decisions/0008-test-and-coverage-engine.md)     | Test and coverage engine                                                                     |
| [0009](decisions/0009-python-profile.md)               | Python profile: Ruff, Pyright, pytest, coverage.py                                           |
| [0010](decisions/0010-launcher-bootstrap-and-hooks.md) | Launcher, bootstrap, doctor, and managed hooks                                               |
| [0011](decisions/0011-typescript-profile.md)           | TypeScript profile: Prettier, ESLint, tsc, Vitest                                            |
| [0012](decisions/0012-dart-profile.md)                 | Dart profile: dart format, dart analyze, package:test, package:coverage                      |
| [0013](decisions/0013-php-profile.md)                  | PHP profile: Composer, PHP CS Fixer, PHPStan, PHPUnit with Xdebug                            |
| [0014](decisions/0014-behavioral-scenarios.md)         | Behavioral scenarios: capabilities, manifests, proof, and quarantine                         |
| [0015](decisions/0015-ci-wrappers-and-merge-gates.md)  | CI wrappers, the required status, dependency audit, and control files                        |
| [0016](decisions/0016-template-lifecycle.md)           | Template lifecycle: export, components, tool updates, and adapter contracts                  |
| [0017](decisions/0017-browser-frontend-profile.md)     | Browser frontend profile: React, Vite, component model, versions, static checks, unit suites |
| [0018](decisions/0018-browser-builds-and-scenarios.md) | Browser builds, Playwright scenarios, accessibility, and the browser CI job                  |

## Guides

- [Test quality](guides/test-quality.md): what counts as test evidence and how
  to write tests that prove scenarios.
- [Hosting protection](guides/protection.md): the prepared ruleset and code
  owners, how to apply and confirm them, and the real trust boundary.
- [Architecture](guides/architecture.md): components, kinds, activation,
  scope, and the layers of the core.
- [Exceptions](guides/exceptions.md): when an exception is justified and how
  to declare, review, and retire it.
- [Extending](guides/extending.md): adding a language profile or a coverage
  format, and the contract tests it must pass.
- [Maintenance](guides/maintenance.md): updating pinned tools, packages, the
  policy, and a project from a newer template.

## Reference

- [Command-line reference](reference/cli.md): commands, options, exit codes,
  and JSON reports.
- [Profile support matrix](reference/profiles.md): languages, manifests,
  native tools, coverage formats, and system requirements.
- [Policy JSON Schema](reference/policy.schema.json), generated from the
  policy models; the policy itself is [`quality.toml`](../quality.toml).
- [Scenario manifest JSON Schema](reference/scenarios.schema.json), generated
  from the manifest models.
- [Quality core](../tools/quality/README.md): layout, exit codes, and native
  development commands.
- [Agent instructions](../AGENTS.md): setup and the everyday commands.
- [Reference browser application](../examples/web/README.md): the React +
  Vite example component and the scenarios it proves.
