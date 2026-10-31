# 0011 TypeScript profile

Status: accepted (P06).

## Scope

The TypeScript profile checks a component with the pinned Prettier, ESLint
with typescript-eslint, the TypeScript compiler, and Vitest with V8 coverage
([decision 0001](0001-toolchain-pins.md)). The structure metrics and module
architecture are the common ones of [decision 0007](0007-common-gates.md); the
test and coverage engine is [decision 0008](0008-test-and-coverage-engine.md),
and the coverage semantics are in
[decision 0005](0005-coverage-measurement.md#typescript-vitest-505-with-v8-provider).

Node.js is a pinned tool of `tools/quality/toolchains.toml`, verified by
SHA-256 and unpacked whole into the toolchain cache. The npm packages are
pinned in the same manifest under `[packages.npm]`; every component locks them
at those versions in its own `package-lock.json`, because the tools import the
component's configuration and code. Node is installed only when a TypeScript
component needs it, so a repository without one never downloads it.

Manifest dependency specifications must match the root and workspace entries
of `package-lock.json` before installation. Drift reports `typescript.lockfile`
as a candidate violation (exit 1), with a remedy to run `npm install` in the
lockfile directory and stage the result. A cache miss for consistent manifests
remains an environment failure (exit 3) requiring bootstrap.

## Component contract

Before any tool runs, the profile reads the component's configuration from the
candidate snapshot. A component that fails this check is not formatted,
linted, typed, or tested. Configuration findings are `typescript.config`;
manifest drift is `typescript.lockfile`.

| File | Requirement |
| --- | --- |
| `package.json` | A JSON object at the component root |
| `package-lock.json` | In the component root, or in the nearest ancestor whose `package.json` lists the component in `workspaces`; lockfile version 2 or 3 |
| Locked tools | `typescript`, `eslint`, `typescript-eslint`, `prettier`, `vitest`, and `@vitest/coverage-v8` resolve, the way Node resolves them from the component directory, to the pinned versions; `@eslint/js` too when it is locked |
| `tsconfig.json` | At the component root |
| ESLint | A flat configuration `eslint.config.{js,mjs,cjs,ts,mts,cts}` at the component root; no legacy `.eslintrc*` |
| Sources | No JavaScript files among the source and test files: the profile type-checks and measures TypeScript only |

## Environments

Every check installs each lock directory once per snapshot work area: the
pinned Node runs its bundled npm as `npm ci --offline --ignore-scripts
--no-audit --no-fund` against the shared npm cache in the toolchain cache.
Install scripts never run. The user's and the global npm configuration are
replaced by empty files, and command-line options override a project
`.npmrc`. Checks never download: a package missing from the cache is an
environment error that names `./quality bootstrap`, which fills the cache by
running the same install online in a throwaway copy of the package manifests
([decision 0010](0010-launcher-bootstrap-and-hooks.md)).

## Static checks

The gate passes explicit file lists: the component's TypeScript source and
test files (`.ts`, `.tsx`, `.mts`, `.cts`), and for formatting also the
JavaScript and TypeScript configuration scripts at its root, such as
`eslint.config.js`. JSON files are not formatted.

| Rule | Check |
| --- | --- |
| `typescript.format` | `prettier --list-different` with an empty ignore file, so `.prettierignore` cannot hide a file |
| `typescript.lint` | Every file has an ESLint configuration with type information (`projectService` or `project`); a file the configuration ignores or does not match is a finding; `eslint --format json --no-ignore`; every message, warnings included, is an error |
| `typescript.types` | The compiler API over `tsconfig.json` and its project references, checked from source without build output; every project sets `strict: true` explicitly (directly or through `extends`, not by a compiler default) and turns none of the strict family off; every source and test file must belong to a project |

The compiler and ESLint checks run through a helper in
`tools/quality/helpers/typescript`, a TypeScript program that Node runs
directly. It has no runtime dependencies: it loads TypeScript and ESLint from
the component's own installation and answers in JSON. The helper is a
tooling component of this repository, checked by this profile, and its
sources are judged with the core
([decision 0004](0004-candidate-snapshots.md#validator-provenance)).

## Structure and architecture

The helper parses each source and test file with the TypeScript parser and
applies the counting rules of decision 0007:

- functions are declarations, expressions, arrow functions, methods,
  constructors, and accessors with a body; overload signatures and abstract
  members are skipped;
- complexity adds one for each `if`, conditional expression, `for`, `for-in`,
  `for-of`, `while`, `do`, `catch`, non-default `case`, `&&`, `||`, `??`, and
  logical assignment;
- nesting counts `if`, loops, `switch`, and `try`; an `else if` chain stays at
  one level;
- parameters exclude a TypeScript `this` parameter;
- complexity and nesting count the function's own body: a nested function is
  measured on its own, and so is a nested class;
- a function's symbol is its name qualified by the classes and named object
  literals around it, such as `Parser.parse` or `handlers.onOpen`; a function
  without a name takes the name of the variable, property, or default export
  it is assigned to, else `<anonymous>`. A decorated method starts at its
  first decorator.

Imports (static, dynamic with a literal, `require`, and `export from`) are
resolved with the module resolution of the project that owns the file, so
`paths` aliases and references are followed. Edges into other source files of
the component feed the module architecture; imports that resolve into
`node_modules`, including another workspace package, are not edges.

Structure and architecture findings of a TypeScript component come from the
helper, so they exist only when the profile checks the component. An exception
for such a finding is reported unused only when its component was checked.

## Suites

Vitest runs with a configuration the gate writes into the run's output
directory. It imports the component's `vitest.config.*` or `vite.config.*`
and overrides what decides the verdict: the test files (the declared suite
roots with Vitest's default test-file pattern), retries (off), the reporters
(JUnit into the output directory, and the gate's own reporter), and coverage (V8, LCOV into the output directory, the
source roots' TypeScript files included, declaration files excluded). When the
component sets no `oxc` options, `.cts` files are transformed too; tsconfig
`paths` are resolved unless the component configures `resolve.tsconfigPaths`.
The suites of a browser component, except end-to-end, also run in jsdom
with `expect.requireAssertions`
([decision 0017](0017-browser-frontend-profile.md#unit-tests-and-coverage)).

The gate's reporter writes what JUnit cannot say: the tests marked
`test.fails`, which JUnit reports as passing when they fail as expected and
which count as expected failures; the tests that passed only on a retry,
which a test's own `retry` option still allows and which count as failures;
and the number of errors raised outside any test, which fail the suite
([decision 0014](0014-behavioral-scenarios.md)).

Each run gets a new home, cache, and temporary directory in the work area,
like the Python runner.

## Fixes

`./quality fix` runs `prettier --write` and `eslint --fix` in a temporary copy
of the component with an offline install, over the same files as the check,
then copies back each changed file whose working-tree content is still the
copied one. It never stages anything. For a browser component it also
formats the CSS files and HTML entry points
([decision 0017](0017-browser-frontend-profile.md#static-checks)).

## Residual risks

- Tests and configuration files are code: `vitest.config.*` and
  `eslint.config.*` run in the check. They can weaken only what the gate does
  not override, and they are reviewed with the change.
- Unit tests reach loopback only, as for Python
  ([decision 0009](0009-python-profile.md#residual-risks)).
- Coverage is measured by V8 and remapped to the original sources; branch
  semantics follow V8, not the compiler's control flow.
- The type check runs once per project of the reference graph, about 1.5
  seconds each on the reference machine, so a component with many projects
  checks more slowly.
