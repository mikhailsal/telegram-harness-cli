# Releasing and versioning

These rules decide when `thc` is released and which version it gets, so that
no release needs a version question. The acceptance steps themselves are in
the [testing guide](testing.md).

## Where the version lives

The version is the `version` field of `package.json`, repeated in
`package-lock.json`. There is no `VERSION` file and no `.gstack/version-path`;
do not add either. Without them, `/ship` finds no version source and leaves
the version alone, which is the intent: only a release pull request changes
it.

A release is an annotated tag `vX.Y.Z` made by `node scripts/release.ts`, with
a GitHub release whose notes are that version's CHANGELOG section. A version
is tagged once. A pushed tag and its release are never moved or deleted. The
package is private and is not published to npm.

## Every pull request

- A user-visible change adds an entry under `## [Unreleased]` in
  `CHANGELOG.md`, in `### Added`, `### Changed`, `### Fixed`, `### Removed` or
  `### Security`. Create the heading when it is missing. Write for the
  operator: what they can do now, or what stopped failing. Leave out branch
  history, review rounds and `TODOS.md`.
- A change only to tests, CI, internal structure or contributor documents
  needs no entry.
- A breaking change starts its entry with `**Breaking:**` and names the step
  the operator takes. A change is breaking when an existing configuration,
  `.thc` state, command, option, exit code or JSON output field stops working
  as before after an update.
- A pull request never changes the version or a released CHANGELOG section.

## Choosing the version

The `## [Unreleased]` section decides the level. Nobody is asked.

| `## [Unreleased]` holds                                      | Next version after `0.Y.Z` |
| ------------------------------------------------------------ | -------------------------- |
| a `**Breaking:**` entry in any list, or a `### Removed` list | `0.(Y+1).0`                |
| an `### Added` list                                          | `0.(Y+1).0`                |
| only `### Changed`, `### Fixed`, `### Security`              | `0.Y.(Z+1)`                |

`1.0.0` comes only by the owner's decision, once the configuration and state
formats are meant to stay. From then on a breaking change raises MAJOR, an
addition MINOR, and everything else PATCH.

gstack's default is to choose versions without asking and to prefer PATCH. That
default suits a project that stamps a version on every merge. Here releases
collect several pull requests, so the level tells the operator whether a
release brings new commands or only fixes.

## When to release

- **Fix release, the same day:** a merged fix for a bug in the latest tagged
  release that stops the bridge, loses or misroutes messages, or exposes a
  token or another secret. It still takes every release step below, including
  the owner's word on the checklist.
- **Otherwise on the owner's request.** After each merge, the agent's report
  states how many entries `## [Unreleased]` holds and the version they would
  make.
- An open P0 item in `TODOS.md` blocks every release except a fix release for
  that item.

## Release steps

1. **Release pull request.** Branch `release/X.Y.Z`, title
   `chore: release X.Y.Z`. It changes only the version
   (`npm version X.Y.Z --no-git-tag-version`), renames `## [Unreleased]` to
   `## [X.Y.Z] - YYYY-MM-DD`, and turns `**Completed:** unreleased` into
   `**Completed:** vX.Y.Z` in `TODOS.md`. It goes through `/ship` and the
   quality gate like any other pull request and merges with a squash. From
   this merge until the tag is pushed, only fixes for this release merge, so
   the tag holds exactly what `## [X.Y.Z]` lists.
2. **Live acceptance** on the merged commit of `main`: a clean
   `npm run test:live` on Linux x64 with the Node version in `.nvmrc`. It
   needs a logged-in `codex`, a running Codex daemon and `tmux`, and it spends
   the account's Codex quota. The agent may run it on the owner's machine. The
   record it writes binds the commit, so it cannot run on the release branch.
   It also holds a digest of the installed `node_modules`, which `release.yml`
   rebuilds on an `ubuntu-24.04` x64 runner, so on another platform the
   pushed tag fails that check.
3. **Manual Telegram checklist** from the testing guide, with a separate test
   bot. The owner does it: the agent has no Telegram client and must not use a
   real bot. The agent tags only after the owner says the checklist passed or
   says to release without it this time, and states which in its report.
4. **Tag.** Within 24 hours of the acceptance record,
   `node scripts/release.ts` creates the annotated tag, then
   `git push origin vX.Y.Z`. The `release.yml` workflow verifies it.
5. **GitHub release** with the command block at the end of the testing guide,
   after that workflow passes.

`install.sh` still installs the newest commit on `main` (see `TODOS.md`), so a
tag marks a tested point rather than what a new install receives.

## When acceptance or the checklist fails

Do not tag. Fix the problem in an ordinary pull request. Its CHANGELOG entry
goes into the untagged `## [X.Y.Z]` section, because that version was never
released, and the same pull request updates that section's date. Then repeat
from step 2 on the new `main`.

If another pull request merged before the tag anyway, the fix pull request
moves its entries from `## [Unreleased]` into `## [X.Y.Z]`. When those entries
call for a higher level than X.Y.Z by the table above, do not tag X.Y.Z: open
`release/<higher version>` instead, which renames that section.

When `release.yml` rejects a pushed tag, the tag stays and that version gets
no GitHub release. Fix the cause in an ordinary pull request and release the
next patch version.

A problem found after the tag is pushed is fixed in the next release. There
are no maintenance branches before `1.0.0`.

## Acceptance and CI

`npm run test:live` stays a local step. GitHub-hosted runners have no Codex
login, and running it there would need an OpenAI API key as a repository
secret, paid on every run, and a CI job that pushes the tag in place of the
operator. The self-checks of the acceptance harness in
`tests/core/release-acceptance.test.ts` need no Codex and run in every pull
request's `gate` job and in the local hooks.
