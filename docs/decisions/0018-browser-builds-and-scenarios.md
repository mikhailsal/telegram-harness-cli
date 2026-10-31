# 0018 Browser builds, scenarios, and CI

Status: accepted (F06, 2026-10-22); proposed in F00, with the production
build implemented in F03, serving, the browser scenarios, and accessibility
in F05, and the CI lanes in F06. It continues
[decision 0017](0017-browser-frontend-profile.md) with what a browser
component needs at the merge stage.

## Production build

The gate builds the candidate snapshot, never the work tree, with
`vite build` through a generated wrapper, as the Vitest wrapper of
[decision 0011](0011-typescript-profile.md#suites) does:

- the component's Vite configuration is loaded from the snapshot in
  production mode;
- `envDir` is an empty directory of the run, so no `.env`, `.env.local`, or
  `.env.production` file is read; the process environment is the sanitized
  one of the base, without `GIT_*` variables, plus only the declared public
  `VITE_*` values;
- the output goes to a new directory of the run with `emptyOutDir`, so a
  committed or stale `dist/` is never served;
- a boundary plugin records each Node builtin that Vite externalizes for
  the browser, unless the importer's package maps it in its `browser` field,
  each declared server-only module in the module graph, and each module of a
  declared server package imported from outside it; this covers transitive
  and aliased imports. The core reports them as `typescript.boundaries`,
  except imports by the browser sources, which the static check reports;
- Vite's manifest is written, and the evidence records the candidate's
  identity, every output file with its SHA-256, and their combined digest.

A failed build is a `typescript.build` finding. The output is validated as a
`typescript.build-output` finding: every declared entry point is built, and
every script, style, icon, and asset a built page references, and every
`url()` of the built CSS, is a file of the output below the resolved `base`
or a `data:` URL; a reference to another origin is reported too.

## Serving

The gate serves exactly that output with its own static server on
`127.0.0.1` and a port the kernel assigns, so an occupied port or another
application cannot answer for it. A directory without an index, or a path
without an extension, that names no file falls back to the first entry
point for client routing; any other missing file is a 404 and is recorded
before it is answered. The server stops when the suite ends, also on failure
or interruption. A dedicated Linux worker acts as a child subreaper: it owns
Node and Chromium descendants even when they create separate sessions or
outlive their parent. SIGTERM and SIGINT kill those descendants before the
worker unwinds; its finalizer also kills and reaps adopted children after
normal completion, failure, and timeout. Before starting children, the worker
arms a Linux parent-death signal and checks its launching parent's identity
to cover termination during startup. Terminating that CLI therefore also
triggers the worker's cleanup. Cleanup is bounded and an inability
to establish ownership or stop children is an environment failure. This uses
the existing Linux and glibc prerequisite from [decision 0002](0002-installation-strategy.md),
with pidfd support (Linux 5.3 or later, or an equivalent backport). The worker
probes opening and signalling a pidfd before it starts any children and fails
explicitly if unavailable; it never falls back to signalling a reused numeric PID.
The gate never stops a process it did not start.

The server and Playwright start in the same loopback-only network namespace,
using the unit-suite containment facility. An unavailable namespace is
reported as `tests.network`; hosted gate machines must provide it. The
server records successful GET responses for built HTML documents, excluding
HEAD, missing files, and assets, with a fresh random response token. Every
passing test in every viewport must have a document navigation in its network
trace bearing a token the server issued for this build. Requests made through
the API client and documents fulfilled or aborted by test routing cannot supply
that proof. `server.json` retains the tokens and per-test matched counts, the
aggregate counts, and the originating candidate and output digest alongside
`build.json`. A missing match blocks with `typescript.browser-served`, invalidates
the native pass for scenario reconciliation, and discards its accessibility proof.

## Browser scenarios

End-to-end suites of a browser component run Playwright Test through a
generated configuration that imports the component's own and then sets what
decides the verdict:

Component configuration may set `use.viewport`, `locale`, `timezoneId`,
`colorScheme`, `reducedMotion`, `forcedColors`, `contrast`, `deviceScaleFactor`,
`isMobile`, `hasTouch`, `actionTimeout`, `navigationTimeout`, `screenshot`,
`video`, and `testIdAttribute`. The declared viewports still decide the gate's
projects. Unknown `use` keys fail with `typescript.playwright`, naming the
configuration path and key. In particular, `proxy`, `connectOptions`,
`extraHTTPHeaders`, `serviceWorkers`, and `storageState` are rejected.
`launchOptions` accepts only `executablePath`, which the gate overrides;
arguments, proxies, and `ignoreDefaultArgs` are rejected. The gate constructs
launch options itself and blocks service workers. Top-level `globalSetup`,
`globalTeardown`, `webServer`, and `build` are also rejected. These findings
are candidate violations (exit 1), including when Playwright cannot write a
test report after rejecting the configuration.

The remaining forced settings are:

- `baseURL` is the gate's server, so no
  development server and no reused server is tested;
- one project per declared viewport, all Chromium with the pinned
  executable, each test in a new browser context;
- `retries` 0, `forbidOnly`, an output directory of the run, and a gate
  reporter whose results the core parses;
- traces without screenshots or sources, kept outside the repository;
  snapshots are enabled because Playwright's network evidence requires them.
  The core reads document responses, console errors, page errors, and failed
  requests from the traces.

Case names are `file::title path[viewport]`, and a scenario selector of a
browser component must pass in every declared viewport. Skipped, focused
(`tests.focused`), expected-failure, flaky, empty, and stale results keep
their base meaning
([decision 0014](0014-behavioral-scenarios.md#what-runners-may-not-hide)).
A renderer crash fails the test at its next step that needs the page; a
crash after a test's last step goes unnoticed. A console or page error is a `typescript.browser-console` error, and a
same-origin request that the build cannot answer is a
`typescript.browser-resource` error; an exception names the rule, the test
file, and the exact message or path.

### Requirements

A browser component's `ui` capability requires, at the merge stage:

| Requirement | Proof |
| --- | --- |
| `ui.smoke` | The production build starts and renders its main interface |
| `ui.flows` | The declared critical user flows |
| `ui.accessibility` | Axe scans of the declared rendered states |
| `ui.keyboard` | Keyboard operation and focus handling of critical controls |

Only a browser component may declare `ui.accessibility` or `ui.keyboard`.
Each of its scenarios names its tested `boundary`, such as a browser network
route that simulates the backend; the check and the reports repeat it, and
it proves nothing about a real backend's contracts. A component that also
declares backend or database capabilities keeps their real-boundary tests.

## Accessibility

A test proves accessibility by asserting that the required interface is
present and then attaching the raw results of an `@axe-core/playwright`
scan named `accessibility-scan`. The gate checks each attachment: the axe
version is the locked one, the scan ran at least the tags of the
component's `accessibility_tags` (`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`
by default) and turned off no rule, and it checked a page of the gate's
server that had content; otherwise it is a `typescript.accessibility-scan`
error. Serious and critical violations are `typescript.accessibility`
errors, moderate and minor ones warnings, and incomplete results are
reported for manual review. An exception names the axe rule, the test file,
and the target. A scenario of `ui.accessibility` whose test lacks a valid
scan in any viewport fails with `scenarios.evidence`. Scans are evidence for
the selected rules, not a conformance claim.

Visual, bundle-size, and performance budgets are not part of this baseline;
they would be enhanced capabilities with their own measurement and blocking
rules.

## Stages and CI

- Commit stage: the common gates, the static checks, the type check, and the
  unit suites with the floors, over the candidate index.
- Merge stage: all of that again, plus the build, the Chromium scenarios,
  accessibility, the dependency audit, and the history checks.
- `quality gate --stage merge` runs the whole path on any Linux machine; it
  reads no provider variable.

### Lanes

CI runs the gate of one candidate in two required jobs, one lane each
(`quality gate --lane main|browser`):

| Lane | Checks |
| --- | --- |
| `main` | The common gates, the history checks, the changed control files, expired quarantine, and every active component that does not run in a browser |
| `browser` | Every active browser component: its static checks, unit suites, build, end-to-end suites, and scenarios |

Each component is in exactly one lane, and the gate without `--lane` is
both. A lane names what it leaves to the other as `gate.lane` information,
and `ci.lanes` fails a workflow whose required jobs run one lane but not the
other. The same check covers `.gitlab-ci.yml` and the reference GitLab
wrapper in exported projects, including local `extends` chains and merged
global/job variables. An unresolved or unknown lane blocks; a conditional,
manual, or allowed-to-fail job cannot supply a required lane.
Lane proof requires a direct, unconditional `quality gate` or `./quality gate`
command. Comments, quoted text, and commands that merely print gate text do
not count. The shell recognizer supports the wrappers' argument preparation
blocks; conditional gate calls and opaque compound commands cannot prove a
lane. Put each gate invocation on its own line after argument preparation.
When the root locally includes `ci/gitlab/quality.gitlab-ci.yml`, the check
uses [GitLab's include merging](https://docs.gitlab.com/ci/yaml/includes/#merge-method-for-include):
the reference wrapper first, then the root's overrides. A variables-only
override cannot retain the included fragment's stale browser-lane proof.
This bounded composition supports an unconditional reference include;
additional unresolved, conditional, or nested includes block with `ci.lanes`.
The browser lane judges exceptions only by the rules it ran, so an
exception that only the main lane uses is not reported as unused there.

The workflow's `browser` job always runs, without an `if`: the CLI, not the
workflow, decides which components it checks, and without a browser
component it passes with nothing to check. The aggregate requires `gate`,
`browser`, and `audit`, so a failed, cancelled, skipped, or missing browser
job fails it
([decision 0015](0015-ci-wrappers-and-merge-gates.md#the-required-status)).
Its reports are a separate artifact and its coverage baseline a separate
cache key; neither decides the verdict.

Chromium's system libraries are a host prerequisite, like PHP
([decision 0002](0002-installation-strategy.md#prerequisites-documented-not-promised-away)):
bootstrap checks them and fails with `typescript.browser-libraries`, but
does not install them, because the system packages belong to the wrapper's
environment. GitHub's Ubuntu 24.04 runners have them. The GitLab wrapper
installs the set verified in its pinned Ubuntu 24.04 image for every job,
and mirrors the lanes with one hidden job and a `QUALITY_LANE` variable.
Hosted GitHub validation is recorded in
[pull request #3](https://github.com/mikhailsal/ai-boilerplate/pull/3);
the GitLab wrapper has no hosted run.
Hosting settings are prepared, not applied.

## Alternatives rejected

- `vite preview` or Playwright's `webServer`: their ports, reuse, and proxy
  follow the project's configuration.
- Parsing Playwright's console output: traces and a reporter give structured
  results.
- A fixed sleep before the first navigation: readiness is the server's bound
  socket, and Playwright waits for conditions.
