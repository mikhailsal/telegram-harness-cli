# 0017 Browser frontend profile

Status: accepted (F05, 2026-10-22); proposed in F00, with the static checks
implemented in F03, the unit tests and coverage in F04, and the browser
scenarios of [decision 0018](0018-browser-builds-and-scenarios.md) in F05;
amended in F07, when registration gained the end-to-end suite and manifest.

## Scope

The TypeScript profile ([decision 0011](0011-typescript-profile.md)) gains a
second execution target: single-page **React + Vite + TypeScript** browser
applications on Linux (x86_64 and aarch64, glibc), built to static production
output. The baseline test stack is fixed:

- unit, integration, contract, and acceptance suites: Vitest with jsdom,
  React Testing Library, and user-event, with V8 coverage of unit suites
  remapped to the original TS/TSX;
- end-to-end suites: Playwright Test with a pinned Chromium headless shell
  against the production build, and `@axe-core/playwright` scans.

Not supported, and reported as such instead of being checked loosely: other
frameworks (Vue, Svelte, Solid, Preact, Angular), server-side rendering,
React Server Components, Next.js and other meta-frameworks, React Native,
bundlers other than Vite, Sass, Less, and Stylus, Vitest Browser Mode, and
browsers other than Chromium. Each needs its own advertised and tested
capability before it is accepted.

## Component model

- `profile = "typescript"` stays the language profile. A new component field
  `target` is `"node"` (the default) or `"browser"`, so every existing
  configuration keeps its meaning and no migration is needed.
- `target = "browser"` requires `framework = "react"`, `bundler = "vite"`,
  and the `ui` capability. `framework` and `bundler` are invalid without it,
  and `target` is invalid for the Python, Dart, and PHP profiles.
- An optional `[components.browser]` table holds what only a browser
  component has: its HTML entry points (default `index.html`), the declared
  public build-time values (`VITE_*` names with non-secret values), the
  server-only modules and packages its browser code must not import, the
  viewports of its browser scenarios, and the axe tags each accessibility
  scan must run (`accessibility_tags`).
- React rules, DOM globals, the jsdom environment, the build, and the browser
  are applied only to components whose target is `browser`. A Node component
  of the same repository keeps the base checks.
- Ownership is unchanged: the longest root owns a file, and shared source has
  exactly one owner.

### Activation

A shipped reference application has the new role `example`. It is checked
in maintainer mode and when the candidate changes it, and never because an
application uses its profile or target, so an exported Node-only project
gets no browser requirement from it. Helpers keep their rule
([decision 0003](0003-architecture.md)); the browser tools are set up only
for an active component whose target is `browser`.

### Registration

`quality component add <directory> --target browser` registers a React/Vite
application. It is recognized only from its manifests and configuration:
`package.json` declares `react`, `react-dom`, `vite`, and
`@vitejs/plugin-react`, and the directory has a `vite.config.*` and the
HTML entry point. A `.tsx` file or `package.json` alone proves nothing.
Without `--target`, a directory with any of that evidence, or with the
packages of an unsupported framework, meta-framework, or bundler, is
ambiguous and is refused. `--target browser` fails on missing evidence and
on unsupported packages, and adds the `ui` capability, the `e2e` suite,
and the `scenarios.toml` manifest when the directory has them;
`--target node` records the user's choice. The command never edits the project's scripts or
tsconfigs.

## Versions

Read from the npm registry on 2026-10-22 with the pinned Node.js; each was
installed together with `npm install --strict-peer-deps`, which accepted the
set, and a probe application built, linted, type-checked, and ran its unit
and Chromium tests with them.

| Package | Version | Compatibility evidence |
| --- | --- | --- |
| react, react-dom | 19.3.1 | Peer of React Testing Library `^18 \|\| ^19` |
| @types/react, @types/react-dom | 19.3.0 | Matching type packages |
| vite | 8.3.5 | Vitest 5.0.5 accepts `^6.4 \|\| ^7 \|\| ^8`; Node `>=22.12` |
| @vitejs/plugin-react | 6.1.2 | Requires Vite `^8` |
| jsdom | 30.1.3 | Vitest peer `*` |
| @testing-library/react, dom, user-event | 16.3.3, 10.4.2, 14.6.8 | Peers satisfied |
| eslint-plugin-react-hooks | 7.1.1 | ESLint `^3`–`^10` |
| eslint-plugin-jsx-a11y-x | 0.2.0 | ESLint `^9 \|\| ^10` |
| globals | 17.13.0 | Browser and Node global sets for ESLint |
| stylelint | 17.16.0 | Node `>=20.19` |
| stylelint-config-standard, stylelint-config-css-modules | 40.0.0, 4.6.0 | Stylelint `^17` |
| @playwright/test | 1.64.1 | Expects Chromium headless shell revision 1248 |
| @axe-core/playwright | 4.14.0 | Locks axe-core 4.14.0 |
| Chrome headless shell | 156.0.8078.4 | Revision 1248 in Playwright's `browsers.json` |

The existing pins stay: TypeScript 6.0.3, ESLint 10.13.0, typescript-eslint
8.72.0, Prettier 3.9.10, and Vitest with `@vitest/coverage-v8` 5.0.5.

`eslint-plugin-jsx-a11y` 6.10.2, the latest release, declares ESLint up to
`^9`, and `npm install` refuses it beside ESLint 10.13.0 (`ERESOLVE`).
Overriding the peer range or `--legacy-peer-deps` would ignore that failure,
and dropping the rule family would hide it. The maintained fork
`eslint-plugin-jsx-a11y-x` 0.2.0 declares ESLint 10 and ships the same 36
rules under the `jsx-a11y-x/` prefix; the probe reported `alt-text`,
`click-events-have-key-events`, and `no-static-element-interactions` from
it. The upstream plugin replaces it once a release accepts ESLint 10.

### Chromium

The browser is a pinned tool in `tools/quality/toolchains.toml`, installed
by `quality bootstrap` into the quality cache, never into Playwright's own
cache. Playwright 1.64.1 downloads the Chrome for Testing headless shell
156.0.8078.4 from Google's `chrome-for-testing-public` bucket and verifies no
checksum; Google publishes no SHA-256 for it. Both archives were downloaded
and matched the MD5 digests the bucket publishes (`x-goog-hash`); their
SHA-256 values are pinned:

| Archive | Size | SHA-256 |
| --- | --- | --- |
| `linux64/chrome-headless-shell-linux64.zip` | 125797539 | `c3e50596aef52c58f8fda3c6b3411c33d34c88b177b3ed1c2243359fd3b8ba20` |
| `linux-arm64/chrome-headless-shell-linux-arm64.zip` | 125711061 | `81ab9d4d3c301dc5fdfb6c541b9e6632b16fc819a6a854460f40a76fe2b2c476` |

Without a vendor checksum file the tool names no `checksums`, so
`quality tools update` refuses it and an update is a reviewed edit with the
same proof.

Only an active browser component needs the browser: bootstrap installs it
while it caches that component's packages, and doctor locates it without
downloading. Both then probe it against the component's own offline install
of the lockfile. `ldd` must find every library the headless shell links, or
`typescript.browser-libraries` names the missing ones; the `deb.deps` and
`rpm.deps` files beside the binary list the distribution packages. The
binary must start and print its version with `--version` in a throwaway
home. That version must equal the `chromium-headless-shell` entry of the
`browsers.json` of the `playwright-core` that the locked `@playwright/test`
loads, or `typescript.browser-version` fails. A browser that passes is
reported as the information finding `typescript.browser-ready`, which names
its path.

## Static checks

Implemented. They run in every check of a browser component.

- Prettier checks the TS/TSX files, the configuration scripts, the CSS
  files, and the declared HTML entry points; a page Prettier cannot parse is
  a `typescript.format` finding. `quality fix` formats them too; it reads no
  policy, so it also formats the CSS of a component nested below the root.
  Stylelint findings are left to the check.
- Typed ESLint runs as for Node components. The helper's `eslint-config`
  command also returns the severity of each requested rule for each file, so
  every browser source file must enable `react-hooks/rules-of-hooks` and
  `react-hooks/exhaustive-deps`, every TSX/JSX source file the 18
  `jsx-a11y-x` rules of `ACCESSIBILITY` in
  `tools/quality/src/quality/adapters/typescript/browser.py`, and every test
  file `@typescript-eslint/no-floating-promises`, which rejects an unawaited
  user-event call. Warning or error enables a rule, because the gate fails on
  every ESLint message.
- `tsc` runs through the helper over `tsconfig.json` and its references,
  as for Node components, so a type error that Vite would still transpile
  fails `typescript.types`.
- The base structure limits apply unchanged. JSX element depth is not
  control-flow nesting, and render functions are measured like any other.
- Import boundaries (`typescript.boundaries`): the helper's `imports`
  command resolves every import of the browser source files with the
  component's tsconfig, `paths` included, and marks Node builtins and
  type-only imports. A browser source file may not import a Node builtin, a
  file that matches `server_modules` (globs relative to the component root),
  or a package in `server_packages`. `import type` and `export type` load
  nothing and are allowed; `import { type X }` is a runtime import. Tests,
  configuration scripts such as `vite.config.ts`, and the server modules
  keep their Node environment; what packages import is the build's check.
- Stylelint (`typescript.styles`) checks every CSS file with a configuration
  the gate writes into the work area: it extends
  `stylelint-config-standard`, `stylelint-config-css-modules`, and then the
  component's `.stylelintrc*` or `stylelint.config.*`, so the baseline cannot
  be left out. A `stylelint` field in `package.json` is a finding instead of
  being ignored. Ignore files do not apply, an ignored file, an invalid option,
  and a needless or invalid-scope disable comment fail, and every warning is
  an error. A `stylelint-disable` or `prettier-ignore` comment in CSS, and a
  `prettier-ignore` comment in HTML, is a `suppressions.inline` finding like
  any other marker. A Sass, Less, or Stylus file is an unsupported variant.
- Entry points (`typescript.entries`): each declared entry must be a file of
  the candidate, and each script `src` and stylesheet, icon, manifest, and
  preload `href` must resolve to a file of the component as Vite resolves
  it: `/path` from the root or `public/`, other paths from the page's
  directory. A `data:` URL is inline; another origin fails, and so does a
  `%VITE_*%` value that `public_env` does not declare.
- Classification: `dist/` at a browser component's root is generated output
  and `public/` holds assets, outside its source and test roots; source maps
  are generated everywhere. CSS, HTML, SVG, and images are assets: they keep
  the blob, secret, and path checks and get no coverage counters.

## Unit tests and coverage

Every suite of a browser component except end-to-end runs Vitest with the
jsdom environment and `expect.requireAssertions`, through the generated wrapper of decision
0011, so a test that asserts nothing fails; a test file's own
`@vitest-environment` comment still applies. They run in the commit gate on
the candidate snapshot like any unit suite: without services, with network
access limited to loopback. The expected sources are every TS/TSX file of the
source roots except `.d.ts` files, imported or not, so an untested entry
point fails. Coverage is the V8 data remapped to the original files, where a
conditional in JSX is a branch of its line, with the base floors of
`quality.toml` per component. A file another component owns counts only in
its owner's suite, even when this component's tests run it. End-to-end
suites measure no coverage, so they cannot raise the unit numerator.

The production build, the browser scenarios, accessibility, and CI are in
[decision 0018](0018-browser-builds-and-scenarios.md).

## Alternatives rejected

- Upstream `eslint-plugin-jsx-a11y` with a peer override: accepts a declared
  incompatibility.
- Playwright's `playwright install`: verifies nothing and writes to a shared
  user cache outside the pins.
- Vitest Browser Mode as the baseline: it would change the unit-suite and
  coverage contract; it remains a possible optional profile.
- A frontend runner beside the engine: the profile, scenario registry, and
  CLI of the base are extended instead.
