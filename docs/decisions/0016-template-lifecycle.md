# 0016 Template lifecycle

Status: accepted (P11).

## Scope

How a project starts from the template, grows components, and takes later
updates, without editing the engine and without the template's maintainer
content. The export is in `quality/template/`, the policy edit of
`component add` in `quality/policy/edit.py`, tool updates in
`quality/toolchain/update.py`, and their commands in `quality/cli/`.

## Two modes

The template's own repository sets `repository.mode = "maintainer"`: every
helper bundle is active, and the core's acceptance suite, which drives real
hooks in scratch repositories and exercises this lifecycle, is declared as a
test suite. A project runs in the default project mode: a helper is active
only when an application uses its profile or the candidate changes the
helper ([decision 0003](0003-architecture.md#roles-and-activation)). The
separation is declared scope in the policy, not a skip: the project's policy
simply has no acceptance suite and no maintainer mode
([decision 0014](0014-behavioral-scenarios.md) for scenario scope).

## Export

`./quality template export DIRECTORY [--rev REV]` writes the tree of a
commit, never the working tree, to a new or empty directory:

- paths matching `[template].exclude` (the plan records, the acceptance
  harness, and the template's root `LICENSE`) are left out; a project
  chooses its own license, and `tools/quality/LICENSE` keeps the MIT notice
  with the core it ships;
- every file in `[template].fenced` loses its maintainer-only regions. A
  region starts at a line containing `maintainer-only:begin` and ends at a
  line containing `maintainer-only:end`, both removed with it; in TOML they
  are comments and in Markdown HTML comments. A blank line that would double
  the one before a region is dropped too;
- the source policy must validate, and the export stops with
  `template.invalid` on a fence that does not open and close, a nested
  fence, a fenced file without a fence or not exported, a non-UTF-8 fenced
  file, a submodule, or a symbolic link to a left-out path;
- the result is written to a staging directory beside the target, and the
  exported policy must validate there in project mode.

Only then does the staging directory become a Git repository on the policy's
`default_branch`. Every exported file is committed, ignored ones included,
as the root commit, with the message `Start from the quality template` and a
`Quality-Template: <commit>` trailer naming the template commit. The commit
uses the user's own identity, from the Git identity variables or their Git
configuration, with `user.useConfigOnly` so that Git cannot guess one, and
no hook is bypassed. Without an identity the export fails with exit 3 and
leaves nothing behind. The staging directory is renamed to the target last,
so a failed export never leaves a partial project.

The root commit is the base of the project's first own commit. Bootstrap and
the pre-commit hook therefore see only the user's changes as the candidate,
so the dormant helper bundles stay dormant and their toolchains are never
installed. The trailer is the provenance that later updates diff against
([maintenance guide](../guides/maintenance.md#updating-a-project-from-a-newer-template)).

The launcher prepares uv, CPython, and the core environment for
`template export` as it does for `bootstrap`, because a fresh clone of the
template has none of them yet
([decision 0010](0010-launcher-bootstrap-and-hooks.md)). Every other command
still runs offline.

## Adding a component

`./quality component add ROOT` appends an application component to
`quality.toml`. The profile comes from the manifest at the root
(`--profile` chooses when several are present); the source and unit test
directories default to the profile's conventions and must exist. A
component declares its boundaries with `--capability` or explains their
absence with `--no-boundary-reason`. The edit appends text, so comments and
order stay. The edited policy must validate, including overlap and identity
conflicts with existing components, before the file is written; nothing is
staged. A policy that keeps its components in an inline array is refused
rather than rewritten.

For Python, registration also creates a missing `.python-version` from the
core's interpreter pin at `tools/quality/.python-version`. An existing file
stays unchanged. The policy must pass validation before the pin is created;
stage it with the component before bootstrap and the first commit.

## Updating pinned tools

`./quality tools update NAME VERSION` rewrites one tool of
`toolchains.toml` for another release. Each tool names its vendor's checksum
file in `checksums`, an HTTPS URL or `{url}` plus a suffix, with `{version}`
placeholders. New URLs and members are the old ones with the version
replaced; checksums come from the vendor file, and extra files are
downloaded and hashed. Only that tool's lines change, and the result must
parse to the old manifest with only that tool replaced. Errors are
`tools.update-invalid` (exit 2) and `tools.update-failed` (exit 3); nothing
is written on either. The gitleaks rule set is fetched at the release tag,
so its URL follows the version like the archives.

## Adapter contracts

`tests/unit/test_profile_contract.py` runs one set of tests over every
`Profile` member: each stage knows the profile, its rules are registered and
explained, it expects coverage of its own sources only, an empty or
malformed coverage report never passes, and source of another or an
unsupported language fails classification. A new profile that misses a
stage fails these tests before any project sees it
([extension guide](../guides/extending.md)).

## Consequences

- A project starts with one command and one reviewed root commit, and
  installs only the toolchains of the profiles it uses.
- Every change to `quality.toml` affects every component, so adding a
  component also runs the core's own suites; that costs minutes, not a
  bypass. Three tests of the lifecycle acceptance test each run such a
  nested gate, up to nine minutes on a workstation; they are independent, so
  they run on parallel workers
  ([decision 0009](0009-python-profile.md#suites)). A Python suite may run
  for two hours before it times out, so that a machine with few CPUs still
  finishes.
- Updating a project from a newer template is a reviewed three-way apply of
  the difference between two exports, not an automatic merge.
- The decision records stay in the export as the design history; they
  mention the maintainer harness that the export leaves out.
