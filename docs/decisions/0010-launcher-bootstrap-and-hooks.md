# 0010 Launcher, bootstrap, doctor, and hooks

Status: accepted (P05); amended in P06, P07, P08, and P11.

## Launcher

`./quality` is a symlink to `tools/quality/launcher/src/quality_launcher.py`.
It runs on the distribution's `python3` (3.10 or newer) with the standard
library only, so it works before anything is installed. The launcher is its
own tooling component, `quality-launcher`, whose tests run on the pinned
CPython with Ruff targeting Python 3.10.

- `./quality bootstrap` installs the pinned uv from a SHA-256-verified
  download ([decision 0002](0002-installation-strategy.md)), installs the
  pinned CPython with `uv python install --no-bin`, and syncs the core
  environment with `uv sync --frozen`. Then it runs the core's `bootstrap`
  command. Since P11 `./quality template export` prepares the core the same
  way, because a fresh clone of the template has nothing installed yet
  ([decision 0016](0016-template-lifecycle.md#export)).
- Every other command first runs `uv sync --frozen --offline --quiet` in
  `tools/quality`. A failure is exit 3 with the instruction to run
  `./quality bootstrap`; nothing is downloaded.
- The launcher then replaces itself with the core interpreter in isolated
  mode (`-I`). It drops `VIRTUAL_ENV`, `PYTHONPATH`, `PYTHONHOME`, and
  `UV_PROJECT_ENVIRONMENT`, and keeps `GIT_INDEX_FILE`, which hooks need.
- `check`, `test`, and `self-test` are judged by the candidate's own core. When
  the working-tree core differs from it, the candidate's core runs the
  command as a child process
  ([decision 0004](0004-candidate-snapshots.md#validator-provenance)). That
  child starts with the same boot statement as the launcher; a unit test keeps
  the two copies identical.

There is no flag, environment variable, or permissive mode that skips a check.
The construction order of P00–P04, which validated code with native commands
before this gate existed, did not add one.

## Bootstrap

The core's `bootstrap` command, after the launcher step:

1. installs gitleaks, verified against `tools/quality/toolchains.toml`;
2. for each component of the staged candidate, fills the package cache that
   the offline checks install from:
   - a Python component: installs its interpreter and syncs its lockfile once
     with network access into a throwaway environment, which fills uv's cache
     ([decision 0009](0009-python-profile.md));
   - a TypeScript component: installs the pinned Node, then runs `npm ci` once
     with network access in a throwaway copy of the lockfile and the package
     manifests of the lock directory and its workspace members. The project's
     `.npmrc` is not copied, so the install uses the public registry without
     credentials ([decision 0011](0011-typescript-profile.md#environments)).
     Each lock directory is installed once, even when several components
     share it;
   - a Dart component: installs the pinned Dart SDK, runs `dart pub get
     --enforce-lockfile` once per lock directory with network access in a
     throwaway copy of its manifests and those of its workspace members, and
     compiles the structure helper
     ([decision 0012](0012-dart-profile.md#environments));
   - a PHP component: installs the pinned Composer, probes the system PHP and
     Xdebug against the manifest's `[runtimes]`, runs `composer install
     --no-scripts --no-plugins` once per lockfile with network access in a
     throwaway copy of `composer.json` and `composer.lock` (plus the
     manifests of path packages inside the repository), and installs the
     structure helper's locked packages
     ([decision 0013](0013-php-profile.md#environments));
3. installs the managed `pre-commit` hook, and the `pre-push` hook with
   `--pre-push`.

Components are read from the staged candidate in an isolated snapshot, the
same view the pre-commit check uses, honoring `GIT_INDEX_FILE`. Bootstrap
never stages, commits, or rewrites a tracked file, and running it again is
idempotent.

## Managed hooks

Hooks are installed only in the repository's own hooks directory
(`git rev-parse --git-path hooks`). Each one carries the marker
`# quality-managed-hook: <name> v1` on its second line and only calls the
launcher:

| Hook | Runs |
| --- | --- |
| `pre-commit` | `<hooks>/pre-commit.local` if executable, then `./quality check --staged` |
| `pre-push` | `<hooks>/pre-push.local` if executable, then `./quality check --rev <sha> --all` for every pushed ref that is not a deletion |

A failing local hook stops the managed hook. Hooks check and report; they
never fix or stage anything.

Conflicts are never resolved by force. A hook file without the marker, or a
`core.hooksPath` setting, is `hooks.conflict` (exit 3): the file is left
unchanged and nothing is installed. The remedy is to rename the existing hook
to `<name>.local`, or to unset `core.hooksPath`, and run bootstrap again.

## Doctor

`./quality doctor` checks, offline and without changing anything:

- Git is 2.34 or newer (`git.version`);
- the core interpreter, uv, gitleaks, Ruff, and Pyright are installed;
- every component of the staged candidate passes its configuration check and
  installs offline (`uv sync --offline` for Python, `npm ci --offline` with
  the installed Node for TypeScript, `dart pub get --offline` with the
  installed SDK for Dart, `composer install` with the network disabled and
  the system PHP and Xdebug probed for PHP; Node, Dart, and Composer are
  located, never downloaded);
- the `pre-commit` hook is current: missing or outdated is `hooks.missing`,
  a foreign file is `hooks.conflict`. The optional `pre-push` hook is listed;
  only an outdated one warns.

It prints one line per check and exits 3 when something is not ready.

## Other commands

| Command | Behavior |
| --- | --- |
| `./quality fix` | Safe native fixes and formatting in the working tree; prints what remains and that nothing was staged |
| `./quality test` | Runs the declared suites (`--suite`, unit by default) of the affected components, every active one with `--all`, or those named with `--component` |
| `./quality self-test` | Runs the unit (`--level unit`) or acceptance (`--level acceptance`) suites of the tooling components |

The acceptance suite of the core drives real `git commit` and `git push`
calls in scratch repositories with the hooks bootstrap installed.

## Limits

- Local hooks are advisory: `git commit --no-verify`, deleting a hook, or
  setting `core.hooksPath` skips them. The CI gate (P10) runs the same checks
  on the server side.
- The scratch acceptance repositories run the core under test through a
  small entry point, not the launcher. The launcher itself is covered by its
  own unit suite and by this repository, whose hooks run through it. Since
  P11 the lifecycle acceptance test runs the real launcher of a template
  clone and of the project it exports, and the core's end-to-end suite
  exports a project from a fresh clone.
