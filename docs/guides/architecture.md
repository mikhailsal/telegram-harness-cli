# Architecture

How the gate sees a repository: components, their roles, which of them a
change activates and affects, and how the core is layered. The decisions
behind it are [0003](../decisions/0003-architecture.md) and
[0006](../decisions/0006-discovery-and-classification.md); every value
mentioned here lives in [`quality.toml`](../../quality.toml).

## Components

A component is a directory declared in `[[components]]` with a profile, a
role, its source directories, and its test suites. Every source file in the
repository belongs to exactly one component; a source file or manifest
outside every component fails discovery, so nothing is skipped by being
forgotten. Documentation, configuration, and data files may live anywhere.

Keep components small enough that a change touches few of them. A component
that `depends_on` another is tested again whenever the other changes.

## Roles

| Role | What it is | When it runs |
| --- | --- | --- |
| `application` | Your own code | Always |
| `tooling` | The quality core and its launcher | Always |
| `helper` | A native helper bundle in `tools/quality/helpers` that a profile runs | When an application uses its profile, when the candidate changes its files, or in maintainer mode |

An exported project runs in project mode, so the TypeScript, Dart, and PHP
helpers stay dormant, with no toolchain installed, until the project adds a
component of that profile. Changing a dormant helper's files activates it,
so its own checks run before the change is committed.

## Activation and scope

A check runs the components that are both active and affected:

- **Active**: every application and tooling component, plus the helpers
  whose condition above holds.
- **Affected**: the components that own a changed path, and the components
  that depend on them. A change to `quality.toml`, to the core under
  `tools/quality`, or to any non-documentation path outside every component
  affects every component, because its effect cannot be bounded.

`./quality check --all` covers every active component. Bootstrap and doctor
read the same staged candidate, so they set up exactly the toolchains the
next commit needs.

## Layers of the core

The core under `tools/quality/src/quality` is layered: a package imports
only packages below it. The order is declared in the core component's
`[components.architecture]` table and enforced by `quality check`, from
`cli` at the top to `diagnostics` and `strictjson` at the bottom.

Profiles are adapters: each reads its component's native configuration,
runs the native tools through the process layer, and returns normalized
findings, coverage, and test results that the common rules judge. A project
declares the layers of its own components in the same table.

## Where to change things

| Change | Where |
| --- | --- |
| Thresholds, components, classification, exceptions | [`quality.toml`](../../quality.toml) |
| Pinned tools and packages | [`toolchains.toml`](../../tools/quality/toolchains.toml), see the [maintenance guide](maintenance.md) |
| A new language or coverage format | The core, see the [extension guide](extending.md) |
| Behavioral scenarios | The component's scenario manifest ([decision 0014](../decisions/0014-behavioral-scenarios.md)) |
