"""`quality template export`: write a committed tree as a project template (decision 0016)."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from quality.diagnostics import Diagnostic, Kind, Location, Rule
from quality.gitio import Repository, TreeEntry, create_repository, materialize
from quality.policy import Policy, TemplatePolicy, cross_validate, load_policy
from quality.template.fences import FenceError, strip_fences

TEMPLATE_INVALID = Rule(
    id="template.invalid",
    title="The project template cannot be exported",
    requirement="R10",
    explanation=(
        "The export writes the committed tree without the paths in [template].exclude and "
        "without the maintainer-only regions of the files in [template].fenced. A fence that "
        "does not open and close, a fenced file that is not exported, a submodule, a link to a "
        "left-out path, or an exported policy that does not validate stops the export before "
        "anything reaches the target directory."
    ),
    remedy="Fix the fence or the [template] table, or name a new or empty target directory.",
    kind=Kind.USAGE,
)
RULES = (TEMPLATE_INVALID,)
STAGING_PREFIX = ".quality-template-"
# The trailer of the project's root commit that names the template commit it came from.
TRAILER = "Quality-Template"


class ExportError(Exception):
    """The reasons an export stopped; the target directory is unchanged."""

    def __init__(self, diagnostics: Sequence[Diagnostic]) -> None:
        super().__init__("; ".join(d.message for d in diagnostics))
        self.diagnostics = tuple(diagnostics)


@dataclass(frozen=True, slots=True)
class Exported:
    """What an export wrote: the new repository, its branch, and its root commit."""

    target: Path
    files: int
    left_out: int
    fenced: tuple[str, ...]
    branch: str
    commit: str


def _fail(message: str, path: str | None = None, line: int | None = None) -> ExportError:
    location = Location(path, line) if path is not None else None
    return ExportError([TEMPLATE_INVALID.diagnostic(message, location=location)])


def _excluded(path: str, template: TemplatePolicy) -> bool:
    pure = PurePosixPath(path)
    return any(pure.full_match(pattern) for pattern in template.exclude)


def _plan(entries: Iterable[TreeEntry], template: TemplatePolicy) -> tuple[list[TreeEntry], int]:
    kept: list[TreeEntry] = []
    left_out = 0
    for entry in entries:
        if entry.is_gitlink:
            msg = "a submodule cannot be exported; its files are not in this tree"
            raise _fail(msg, entry.path)
        if _excluded(entry.path, template):
            left_out += 1
        else:
            kept.append(entry)
    files = {entry.path for entry in kept if entry.is_file}
    for path in template.fenced:
        if path not in files:
            msg = "is declared in [template].fenced but is not an exported file"
            raise _fail(msg, path)
    return kept, left_out


def _source_policy(root: Path, rules: frozenset[str]) -> TemplatePolicy:
    loaded = load_policy(root)
    if loaded.policy is None:
        raise ExportError(loaded.diagnostics)
    problems = cross_validate(loaded.policy, rules)
    if problems:
        raise ExportError(problems)
    return loaded.policy.template


def _fenced_text(source: Path, path: str) -> bytes:
    try:
        text = source.read_bytes().decode("utf-8")
        return strip_fences(text).encode("utf-8")
    except UnicodeDecodeError as error:
        msg = "a fenced file must be UTF-8 text"
        raise _fail(msg, path) from error
    except FenceError as error:
        raise _fail(str(error), path, error.line) from error


def _write(source: Path, target: Path, entries: Iterable[TreeEntry], fenced: set[str]) -> None:
    for entry in entries:
        origin, copy = source / entry.path, target / entry.path
        copy.parent.mkdir(parents=True, exist_ok=True)
        if entry.is_symlink:
            copy.symlink_to(origin.readlink())
            continue
        data = _fenced_text(origin, entry.path) if entry.path in fenced else origin.read_bytes()
        copy.write_bytes(data)
        copy.chmod(0o755 if entry.is_executable else 0o644)


def _check_links(target: Path, entries: Iterable[TreeEntry]) -> None:
    for entry in entries:
        if entry.is_symlink and not (target / entry.path).exists():
            msg = "links to a path the export leaves out"
            raise _fail(msg, entry.path)


def _check_policy(target: Path, rules: frozenset[str]) -> Policy:
    loaded = load_policy(target)
    problems = (
        *loaded.diagnostics,
        *(cross_validate(loaded.policy, rules) if loaded.policy else ()),
    )
    if problems or loaded.policy is None:
        raise ExportError(problems)
    return loaded.policy


def _usable(target: Path) -> None:
    if not target.parent.is_dir():
        msg = f"the parent of '{target}' is not a directory"
        raise _fail(msg)
    empty = target.is_dir() and not target.is_symlink() and not any(target.iterdir())
    if os.path.lexists(target) and not empty:
        msg = f"'{target}' exists and is not an empty directory"
        raise _fail(msg)


def export_tree(
    repo: Repository, commit: str, target: Path, rules: frozenset[str], environ: Mapping[str, str]
) -> Exported:
    """Write the project template of `commit` to `target`, a new or empty directory.

    The files are written to a staging directory beside `target`, the exported
    policy is validated there, and the staging directory becomes a repository
    whose root commit, made with the user's Git identity from `environ`, holds
    exactly the exported files. `target` only appears once all of that worked.
    """
    target = target.absolute()
    _usable(target)
    with materialize(repo, repo.tree_of(commit)) as snapshot:
        template = _source_policy(snapshot.root, rules)
        kept, left_out = _plan(snapshot.entries, template)
        staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=target.parent))
        try:
            _write(snapshot.root, staging, kept, set(template.fenced))
            _check_links(staging, kept)
            branch = _check_policy(staging, rules).repository.default_branch
            message = f"Start from the quality template\n\n{TRAILER}: {commit}\n"
            created = create_repository(staging, branch=branch, message=message, environ=environ)
            root = created.resolve_commit("HEAD")
            if target.is_dir():
                target.rmdir()
            staging.rename(target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    return Exported(target, len(kept), left_out, tuple(template.fenced), branch, root)
