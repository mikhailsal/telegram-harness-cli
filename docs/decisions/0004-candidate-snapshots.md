# 0004 Candidate snapshots and revision semantics

Status: accepted (P00); amended in P02, P05, and P06.

## Candidate identity

- Commit stage: the candidate is the index named by `GIT_INDEX_FILE` (or the
  default index). The core copies that file and runs `git write-tree` on the
  copy, so the original index is never rewritten. Like `git commit`, this may
  add unreferenced tree objects to the object database; refs, the index, and
  the work tree are unchanged. Unmerged entries (`git ls-files -u`) reject the
  commit before any other check.
- Merge stage and `--head`: the candidate is the tree of a resolved revision.
- The candidate identity is the tree object ID. Before returning success the
  core recomputes it from the original source; a difference fails with
  `git.candidate-changed`, so a concurrent edit cannot reuse a result.

## Materialization

The snapshot is written to a fresh temporary directory from `git ls-tree -r -z`
and `git cat-file --batch`, which yields exact blob bytes without clean,
smudge, or end-of-line filters:

- `100644` and `100755` become files with the matching executable bit;
- `120000` symlinks must have relative targets; after writing, every link is
  resolved with `realpath`, including chains through other links, and must
  stay inside the snapshot, otherwise `git.symlink-escape` fails the run;
- `160000` gitlinks (submodules) become empty directories; discovery rejects
  them with `discovery.submodule-unsupported` unless they are classified as
  vendor content ([decision 0006](0006-discovery-and-classification.md));
- Git LFS pointer files are materialized as pointers. Pointer text is checked;
  object content is not inspected, which the asset policy states explicitly.

Unstaged edits, untracked files, ignored files, and old reports therefore
cannot influence the result. Native tools search parent directories for
configuration, so the snapshot's parent directories must not contain project
markers such as `quality.toml`, `pyproject.toml`, `conftest.py`,
`package.json`, `node_modules`, `pubspec.yaml`, or `composer.json`; one
found there fails with `git.snapshot-not-isolated`.

Paths with spaces, newlines, or non-UTF-8 bytes are handled as bytes from the
`-z` output.

## Validator provenance

Policy, native configuration, and tests come from the snapshot, and the core
that judges a candidate is the candidate's own. The launcher starts the core
from the working tree, so `check`, `test`, and `self-test` first compare it
with the candidate's `tools/quality`:

- The compared files are `src/quality/**` without `__pycache__`, the
  toolchain manifest `toolchains.toml`, and the TypeScript helper's
  `helpers/typescript/src/**` and `package.json` (since P06; its
  `node_modules` is not compared, because the helper imports TypeScript and
  ESLint from the checked component), and the Dart helper's `bin/**`,
  `lib/**`, `pubspec.yaml`, and `pubspec.lock` (since P07; not its
  `.dart_tool` or its tests). They are compared by mode and blob ID,
  the same way Git identifies them. A candidate without a core package (any
  repository that does not vendor the core) is not compared.
- The candidate's `tools/quality/uv.lock` must equal the lockfile the core
  environment was synced from. A difference fails with `core.environment`
  (exit 3): stage or restore the lockfile, so the working tree and the
  candidate agree.
- Equal files: the running core judges. Different files: the candidate's core
  is materialized, checked again against the candidate (a mismatch is an
  internal error, exit 4), and runs the same command as a child process with
  the caller's directory, environment, and output. Its exit status is the
  verdict.
- A materialized core that still differs from the candidate, because the
  index changed meanwhile, fails with `git.candidate-changed` instead of
  starting another child.

There is no option or variable that skips this comparison. Unstaged edits to
the handlers and everything they call cannot change a verdict in either
direction; the acceptance suite proves both. Other commands, such as `fix`,
`doctor`, and `inventory`, run the working-tree core.

Residual risks:

- The comparison itself (the CLI entry point, the provenance module, and the
  launcher) runs from the working tree, so a local edit there is not defended
  against. CI runs the committed core and is the boundary
  ([decision 0015](0015-ci-wrappers-and-merge-gates.md)).
- `pyproject.toml` of the core is not compared; its environment is defined by
  `uv.lock` alone.
- `--rev` of a commit whose core lockfile differs from the working tree fails
  with `core.environment`. The pre-push hook checks pushed tips this way, so a
  branch with a different core lockfile is pushed from its own checkout.
- An older commit is judged by its own core and rules, including cores from
  before this step existed.
- The child runs in its own process session. Ctrl-C stops it; a parent killed
  with `SIGTERM` or `SIGHUP` leaves it running until it ends or reaches its
  6-hour timeout, like the other subprocesses of the core.

## Environment sanitation

Child processes receive an allowlisted environment. `GIT_*`, `PYTHONPATH`,
`NODE_PATH`, `NODE_OPTIONS`, `PHP_INI_SCAN_DIR`, and similar variables are
removed; the alternate index is translated into a tree ID deliberately and is
never propagated to tests that create their own repositories. Explicit values
(for example the component import roots) are set by the adapter.

## Commit diff

Changed lines for commit validation are the added lines of
`git diff -M <HEAD tree or empty tree> <candidate tree>`. The same comparison
applies to `git commit --amend` (lines changed relative to the commit being
replaced) and to merge commits (lines changed relative to the first parent).

## Merge and pull request revisions

Three revisions are explicit: base `B`, source head `H`, tested snapshot `T`
(defaults to `H`). With `M = merge-base(B, H)`:

- PR-new lines in `T` = lines added by `M..H`, mapped into `T` through
  `diff H T`, plus lines added in both `diff B T` and `diff H T` (conflict
  resolutions).
- Lines that `T` received unchanged from `B` are never PR-new.
- Merge queue (`T` built on `B` from one entry): PR-new lines = added lines of
  `diff B T`.
- Push to a branch: `B` is the previous tip, `H = T` is the new tip; the zero
  revision means an empty tree base.

Missing objects, a shallow history without the merge base, or a `T` that does
not contain `H` fail with `git.revision-unresolvable`; no gate is skipped.

## History range checks

Secret scanning and the blob size limit cover every commit and blob reachable
from `T` but not from `B` (`git rev-list --objects B..T`), so content added and
removed before the tip is still found.
