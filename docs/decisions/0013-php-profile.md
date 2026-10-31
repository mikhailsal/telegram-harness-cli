# 0013 PHP profile

Status: accepted (P08).

## Scope

The PHP profile checks a Composer project. It uses the component's own locked
PHP CS Fixer, PHPStan, and PHPUnit, and Xdebug collects the branch coverage
([decision 0001](0001-toolchain-pins.md#php-profile)). The structure metrics
and module architecture are the common ones of
[decision 0007](0007-common-gates.md). The test and coverage engine is
[decision 0008](0008-test-and-coverage-engine.md), and the coverage semantics
are in [decision 0005](0005-coverage-measurement.md#php-phpunit-1343-with-xdebug-35).

Composer is a pinned tool of `tools/quality/toolchains.toml`: a PHP archive
verified by SHA-256 and run by the system PHP. Bootstrap installs it only
when a PHP component needs it. The packages the gate runs are pinned in the
same manifest under `[packages.composer]`, and every component locks them at
those versions in its own `composer.lock`:

- `phpunit/phpunit`;
- `phpunit/php-code-coverage`, which writes the reports the gate parses;
- `phpstan/phpstan`;
- `friendsofphp/php-cs-fixer`.

The tools autoload the component's code, so they run from the component's
own `vendor/`.

## Runtime

PHP is a system program. The core does not install it, and it runs the `php`
of the standard system directories, never one that an inherited `PATH`
names. `[runtimes.php]` pins its minor version and `[runtimes.xdebug]` pins
the minor version of Xdebug. Prerequisites are in
[decision 0002](0002-installation-strategy.md).

The runtime is probed once per process, with Xdebug off. The probe asks for
the PHP version and the loaded Xdebug version. When the system does not load
Xdebug, the probe asks again with `-d zend_extension=xdebug`, so a machine
that has the extension but does not load it still qualifies.

Xdebug is the only driver accepted, because PCOV and phpdbg measure lines but
no branches. A PHP without Xdebug fails `check`, `test`, and `doctor` with an
environment error that names the prerequisite; a PCOV-only machine is one of
those. Branch coverage is enabled in the generated configuration, so PHPUnit
13 refuses to start when no driver supports it. It then writes an empty
results file, which the runner reports as a run that could not start
(see [Suites](#suites)). A PHP that only claims Xdebug therefore fails as
well.

Every PHP command runs in a sanitized environment:

- `PHPRC` and `PHP_INI_SCAN_DIR` are dropped, so only the system's php.ini
  applies. The CLI reads no php.ini from the working directory.
- Errors go to stderr, so stdout holds only a tool's report.
- Xdebug is off (`XDEBUG_MODE=off`) unless the command collects coverage.

## Environments

Before any tool runs, the profile reads the component's configuration from
the candidate snapshot. A component that fails this check is not formatted,
analysed, measured, or tested. Every finding is `php.config`, located at the
file to fix.

| File | Requirement |
| --- | --- |
| `composer.json` | A JSON object at the component root that `composer validate` accepts. `config.vendor-dir` is `vendor`. Every `autoload` and `autoload-dev` PSR-4 directory is inside the repository |
| `composer.lock` | Beside composer.json and current for it (`composer validate --check-lock`). It locks every `[packages.composer]` package at exactly the pinned version, from a zip archive over HTTPS; Composer's leading `v` in a tag is ignored. Every other archive and source is fetched over HTTPS, and a path package stays inside the repository |
| `vendor/` | Not committed, because Composer installs it from the lockfile |

The component's `.php-cs-fixer.php`, `phpstan.neon`, and `phpunit.xml` are
never read. The first is PHP code that the formatter would execute. The others
could lower the level, add a baseline, or narrow the tests and coverage. The
gate passes its own settings instead:

- PER Coding Style without risky rules;
- PHPStan's highest level with no baseline;
- a generated PHPUnit configuration.

Every snapshot work area prepares each PHP component once. `composer validate
--check-lock` checks the manifest and the lockfile's freshness; a schema error
is a finding at the file Composer names, and nothing is installed. Then
`composer install --no-scripts --no-plugins` installs the locked packages into
the snapshot's `vendor/`. It reads the shared cache
`$XDG_CACHE_HOME/quality/composer` with the network disabled, and runs no
script or plugin of the component. The inventory comes from the candidate's
Git tree and the common gates run before any component is prepared, so the
installation changes neither.

A package missing from the cache is an environment error that names
`./quality bootstrap`. Bootstrap fills the cache by running the same
installation online, in a throwaway copy of composer.json, composer.lock, and
the manifests of in-repository path packages. Doctor repeats that
installation offline ([decision 0010](0010-launcher-bootstrap-and-hooks.md)).

## Static checks

The gate passes the component's `.php` source and test files to both tools,
by absolute path.

| Rule | Check |
| --- | --- |
| `php.format` | `php-cs-fixer check --format=json` with `@PER-CS`, no risky rules, no cache, run sequentially. Each file it would rewrite is a finding |
| `php.types` | `phpstan analyse --level=max --error-format=json` with no baseline. Every error is a finding, and its symbol is PHPStan's error identifier |

PHP CS Fixer skips a file it cannot parse. PHPStan reports that file with the
identifier `phpstan.parse` and then reports no other file until the syntax
error is fixed. An error outside any file, such as an invalid setting, makes
the run a tool failure, not a pass.

PHPStan loads the component's Composer autoloader, which runs the files of
`autoload.files`. It therefore runs behind the network isolation of unit
suites where the system allows it
([decision 0008](0008-test-and-coverage-engine.md#network-isolation)).

Inline `@phpstan-ignore`, `@codeCoverageIgnore`, `phpcs:ignore`, and similar
markers are suppressions, which the common gates reject. A specific
incompatibility that cannot be fixed takes a reviewed `[[exceptions]]` entry
for `php.types` with the exact file and the identifier as its symbol, such as
`parameter.phpDocType`. It covers that identifier in that file only, never a
baseline of errors ([decision 0007](0007-common-gates.md)).

## Structure and architecture

Structure and imports come from a helper in `tools/quality/helpers/php`, a
PHP program built on the pinned nikic/php-parser. The helper has its own
`composer.lock` and is a gate component of this repository.

It is installed once into the cache, in a directory named by a digest of its
manifests, its `bin/` and `src/` files, and the PHP version. Composer installs
its runtime packages there without the development ones. The installation is
built in a temporary directory and renamed into place only when complete, so
a concurrent run never sees part of one.

It answers one JSON request per component and work area with the counting
rules of decision 0007:

- functions are named functions, methods, closures, arrow functions, and
  property hooks. Declarations without a body, such as abstract and
  interface methods, are skipped;
- complexity adds one for each `if`, `elseif`, conditional expression, loop,
  `catch`, `&&`, `||`, `and`, `or`, `??`, and `??=`, and for each `case` and
  `match` arm except `default`;
- nesting counts `if`, loops, `switch`, and `try`. An `elseif` or `else if`
  chain stays at one level;
- a nested function or closure is measured on its own. Its symbol is its name
  qualified by the enclosing classes and functions, such as `Base.run`.
  - A closure takes the name of the variable, property, or named argument it
    is given to, else `<anonymous>`.
  - A hook is named by its kind and property, such as `get label`.
  - An anonymous class is `class@anonymous`.
- a function starts at its first line after attributes and documentation
  comments.

Imports are `use` declarations, class names in code, and `include` and
`require` expressions:

- A class name resolves through the PSR-4 prefixes of the component's
  composer.json to the file that Composer's class loader would read.
- A path resolves when it is absolute, or `__DIR__` followed by a literal.

A name or path that resolves to another file of the snapshot becomes a
dependency edge. Vendor classes resolve to nothing.

The helper also says, for each file, whether it only declares classes,
interfaces, traits, enums, functions, and constants. It also counts the
file's units: its named functions and the methods with a body of its named
classes, traits, and enums. The unit runner uses both answers, so they are
kept per work area.

## Suites

Every suite runs the component's locked PHPUnit with a configuration and a
bootstrap file that the gate writes into the run's output directory:

- The configuration has one test suite made of the suite's existing roots, in
  which files ending in `Test.php` are tests.
- The bootstrap loads `vendor/autoload.php` and then every source file that
  only declares, so a class that no test uses is still compiled and counted.
- A file that runs code when it is loaded is left to the tests.

Unit suites also collect coverage:

- Xdebug runs in coverage mode (`XDEBUG_MODE=coverage`), loaded with
  `-d zend_extension=xdebug` unless the system loads it.
- The source filter is every `.php` file of the source roots, excluding the
  test roots and `vendor/`. Uncovered files are included, branch coverage is
  on, deprecated code units are kept, and `@codeCoverageIgnore` is disabled.
- PHPUnit writes Clover and its JSONL report from the same data.

Neither report holds both counters exactly. Clover has each file's branch
totals, but it writes a method's record in place of the statement record of
the line where the method starts. A one-line method's statement is therefore
counted in the totals but has no record. JSONL has every executable line, but
counts branches only for functions with more than one. The gate takes the
lines from JSONL and the branches from Clover. It cross-checks the two
reports: per-file totals, the files listed, and each Clover statement record
against JSONL. The format is that of the pinned php-code-coverage, which is
why that package is pinned with PHPUnit.

Xdebug measures the branches of a file only when the file is compiled. After
a unit run, each source file that has units but no branch in the report is
marked unmeasured. That fails with `coverage.branch-unmeasured` until a test
loads it; it is never treated as not applicable.

Results are read from JUnit XML, in which an incomplete test is skipped:

- A run that writes no results could not run at all.
- A run that leaves an empty results file could not run either. PHPUnit does
  this when it refuses to start, and the failure quotes its reason.
- Zero tests fail with `tests.none`.
- A missing suite root is `discovery.missing-root`.
- PHPUnit exits 1 when a test failed and 2 when one raised an error. When
  only quarantined tests failed, either status is excused
  ([decision 0014](0014-behavioral-scenarios.md#quarantine)).

Other suites run without coverage and with Xdebug off. Each run gets a new
home, cache, and temporary directory in the work area. A unit suite reaches
loopback only, where the system allows it.

## Fixes

Installing the packages writes `vendor/` into the component, so
`./quality fix` works on a temporary copy of the working tree without
`vendor/`. It validates and installs the lockfile offline there, then runs
the locked `php-cs-fixer fix` on the named files with the gate's settings.
Each changed file whose working-tree content is still the copied one is
copied back. Nothing is staged. What remains, PHPStan's errors included, is
reported as the check reports it.

## Residual risks

- An Xdebug branch is a basic block that counts when it is entered. An `if`
  without `else` whose condition is always true still enters every block, so
  a branch is missed only when a block is never entered: a short-circuit
  operand, a ternary arm, or a `match` arm. The floors apply to what Xdebug
  measures ([decision 0005](0005-coverage-measurement.md#php-phpunit-1343-with-xdebug-35)).
- Two PHP 8.4 compilation details make Xdebug report less coverage than the
  tests reach, never more:
  - Inside a namespace, an unqualified call to a function that PHP compiles
    as frameless, such as `in_array`, adds a fallback block for a namespaced
    function of the same name, and no test enters it. Import the function
    with `use function` or qualify it.
  - A method called only through a first-class callable such as
    `self::check(...)` records its hits under the bare method name, so it
    looks unexecuted. Use a closure instead.
- PHP and Xdebug are pinned to minor versions, so patch versions can differ
  between machines.
- A file that runs code when loaded is measured only when a test loads it.
  The gate fails it as unmeasured instead of trusting it.
- PHPStan reports no other file while one file has a syntax error, so a
  candidate fixing one finding can reveal others.
- PHPStan extensions and included configuration are not loaded, because the
  component's `phpstan.neon` is not read. A rule that needs them takes a
  reviewed exception.
- The core's acceptance suite proves the PCOV refusal with a system PHP that
  loads PCOV instead of Xdebug, so PCOV is a prerequisite of that suite only
  ([decision 0002](0002-installation-strategy.md)).
