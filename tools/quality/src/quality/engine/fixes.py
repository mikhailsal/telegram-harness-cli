"""`quality fix`: native fixers over the working tree; the index is never touched."""

from __future__ import annotations

import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from quality.adapters import native
from quality.diagnostics import Diagnostic
from quality.discovery import active_components, build_inventory
from quality.engine.scope import CHECKED, checked_policy, unavailable
from quality.gitio import Repository, TreeEntry
from quality.policy import Component, Profile

REGULAR, EXECUTABLE = "100644", "100755"


class ProfileFixer(Protocol):
    """A profile's formatter and safe automatic fixes."""

    def fix(self, component: Component, root: Path, files: tuple[str, ...]) -> list[Diagnostic]:
        """Fix the named working-tree files; return the findings that remain.

        Raise ToolFailedError when the fixer cannot run.
        """
        ...


@dataclass(frozen=True, slots=True)
class FixReport:
    """What remains after fixing, and which components were fixed."""

    diagnostics: tuple[Diagnostic, ...]
    fixed: tuple[str, ...] = ()


def worktree_entries(repo: Repository) -> list[TreeEntry]:
    """Tracked and untracked, not ignored, regular files of the working tree."""
    listing = repo.git("ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout
    paths = sorted(set(listing.decode("utf-8", "surrogateescape").split("\0")))
    entries: list[TreeEntry] = []
    for path in filter(None, paths):
        try:
            info = (repo.worktree / path).lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode):
            mode = EXECUTABLE if info.st_mode & stat.S_IXUSR else REGULAR
            entries.append(TreeEntry(mode, "", info.st_size, path))
    return entries


def worktree_changes(repo: Repository) -> list[str]:
    """Paths the index or the working tree changes since HEAD, and untracked files."""
    base = repo.head_commit() or repo.empty_tree()
    changed = repo.git("diff", "--name-only", "-z", "--no-renames", base).stdout
    untracked = repo.git("ls-files", "-z", "--others", "--exclude-standard").stdout
    paths = (changed + untracked).decode("utf-8", "surrogateescape").split("\0")
    return sorted(set(filter(None, paths)))


def fix_worktree(
    repo: Repository, fixers: Mapping[Profile, ProfileFixer], rules: Iterable[str]
) -> FixReport:
    """Fix the source and test files of every active component whose profile has a fixer.

    A dormant helper or example is fixed only when the working tree changes it, as
    bootstrap and check activate it (decision 0003); its tools may not be installed.
    """
    root = repo.worktree
    policy = checked_policy(root, rules)
    if isinstance(policy, tuple):
        return FixReport(policy)
    inventory = build_inventory(policy, root, worktree_entries(repo))
    found: list[Diagnostic] = []
    fixed: list[str] = []
    for component in active_components(policy, worktree_changes(repo)):
        fixer = fixers.get(component.profile)
        if fixer is None:
            found.append(unavailable(component, "fixer for it"))
            continue
        files = tuple(
            record.path
            for record in inventory.files
            if record.component == component.id and record.category in CHECKED
        )
        try:
            found.extend(fixer.fix(component, root, files))
        except native.ToolFailedError as error:
            found.append(error.diagnostic)
            continue
        fixed.append(component.id)
    return FixReport(tuple(found), tuple(fixed))
