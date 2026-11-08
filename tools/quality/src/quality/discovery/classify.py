"""Assign each candidate path a category, an owning component, and a language."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import PurePosixPath

from quality.diagnostics import Diagnostic, Location, Rule
from quality.discovery import rules
from quality.discovery.filetypes import (
    PROFILE_LANGUAGES,
    SUPPORTED_LANGUAGES,
    FileKind,
    FileType,
    has_generated_marker,
)
from quality.policy import Category, Classification, Component, Policy

NATURAL = {
    FileKind.DOC: Category.DOCS,
    FileKind.CONFIG: Category.CONFIG,
    FileKind.ASSET: Category.ASSETS,
    FileKind.GENERATED: Category.GENERATED,
    FileKind.UNKNOWN: Category.ASSETS,
    FileKind.CODE: Category.SOURCE,
}
# Vite's output and public directories in a browser component (decision 0017):
# its bundles are generated, and its public files are copied to the output as
# they are, so they are assets whatever their type.
BROWSER_DIRECTORIES = {"dist": Category.GENERATED, "public": Category.ASSETS}


class Region(StrEnum):
    """Where a path sits inside its owning component."""

    SOURCE = "source"
    TESTS = "tests"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class FileRecord:
    """The classification of one candidate path."""

    path: str
    category: Category
    component: str | None
    language: str | None
    region: Region
    explicit: bool = False


@dataclass(slots=True)
class Classified:
    """A record with the diagnostics found while classifying it."""

    record: FileRecord
    diagnostics: list[Diagnostic] = field(default_factory=list[Diagnostic])
    patterns: list[str] = field(default_factory=list[str])


def under(path: str, root: str) -> bool:
    """Whether `path` is `root` or lies below it; '.' contains everything."""
    return root == "." or path == root or path.startswith(root + "/")


def owner(policy: Policy, path: str) -> Component | None:
    """Return the component with the most specific root containing `path`."""
    owners = [c for c in policy.components if under(path, c.root)]
    return max(owners, key=lambda c: 0 if c.root == "." else len(c.root), default=None)


def relative_to(component: Component, path: str) -> str:
    """Return a repository path relative to the root of the component that contains it."""
    return path if component.root == "." else path.removeprefix(component.root + "/")


def region_of(component: Component | None, path: str) -> Region:
    """Return whether `path` is in the test roots, the source roots, or elsewhere."""
    if component is None:
        return Region.OTHER
    relative = relative_to(component, path)
    if any(under(relative, root) for root in component.tests.all_paths()):
        return Region.TESTS
    if any(under(relative, root) for root in component.source):
        return Region.SOURCE
    return Region.OTHER


def matching(policy: Policy, path: str) -> list[Classification]:
    """Return the classification entries whose pattern matches `path`."""
    pure = PurePosixPath(path)
    return [entry for entry in policy.classification if pure.full_match(entry.pattern)]


def _report(found: Classified, rule: Rule, message: str) -> None:
    location = Location(found.record.path)
    found.diagnostics.append(
        rule.diagnostic(message, location=location, component=found.record.component)
    )


def _natural(found: Classified, filetype: FileType, component: Component | None) -> None:
    record = found.record
    language = filetype.language
    if filetype.kind is FileKind.UNKNOWN:
        if record.region is not Region.TESTS:
            _report(found, rules.UNCLASSIFIED, "file type cannot be determined")
    elif language is None:
        return
    elif language not in SUPPORTED_LANGUAGES:
        _report(found, rules.UNSUPPORTED_LANGUAGE, f"{language} code has no supported profile")
    elif component is None:
        _report(found, rules.UNOWNED_SOURCE, f"{language} code outside every component")
    elif language not in PROFILE_LANGUAGES[component.profile]:
        message = f"{language} file in {component.profile} component '{component.id}'"
        _report(found, rules.LANGUAGE_MISMATCH, message)
    elif filetype.kind is FileKind.CODE and record.region is Region.OTHER:
        message = f"{language} code outside the source and test roots of '{component.id}'"
        _report(found, rules.UNOWNED_SOURCE, message)


def _explicit(found: Classified, entry: Classification, head: bytes) -> FileRecord:
    record = found.record
    if record.region is Region.SOURCE and not (
        entry.category is Category.GENERATED and has_generated_marker(head)
    ):
        message = f"'{entry.pattern}' relabels a file in a source root as {entry.category}"
        _report(found, rules.CLASSIFICATION_RELABEL, message)
        return record
    return FileRecord(
        path=record.path,
        category=entry.category,
        component=record.component,
        language=record.language,
        region=record.region,
        explicit=True,
    )


def browser_category(component: Component | None, path: str, region: Region) -> Category | None:
    """Return the category of a browser component's output or public file outside its roots."""
    if component is None or not component.in_browser or region is not Region.OTHER:
        return None
    relative = relative_to(component, path)
    return next(
        (category for name, category in BROWSER_DIRECTORIES.items() if under(relative, name)),
        None,
    )


def natural_category(region: Region, filetype: FileType) -> Category:
    """Return the category of a path from its region and type alone.

    Configuration scripts such as `setup.py` or `conftest.py` are code: inside a
    source root they are source like any other module there.
    """
    if region is Region.TESTS:
        return Category.TESTS
    if region is Region.SOURCE and filetype.language is not None:
        return Category.SOURCE
    return NATURAL[filetype.kind]


def classify(policy: Policy, path: str, filetype: FileType, head: bytes = b"") -> Classified:
    """Classify one path; explicit classification applies only where it is allowed."""
    component = owner(policy, path)
    region = region_of(component, path)
    category = natural_category(region, filetype)
    record = FileRecord(
        path=path,
        category=category,
        component=component.id if component else None,
        language=filetype.language,
        region=region,
    )
    found = Classified(record)
    entries = matching(policy, path)
    found.patterns = [entry.pattern for entry in entries]
    if len(entries) > 1:
        patterns = ", ".join(f"'{entry.pattern}'" for entry in entries)
        _report(found, rules.CLASSIFICATION_CONFLICT, f"matched by {patterns}")
    elif entries:
        found.record = _explicit(found, entries[0], head)
        if found.record.explicit:
            return found
    special = browser_category(component, path, region)
    if special is not None:
        found.record = replace(record, category=special)
        return found
    _natural(found, filetype, component)
    return found
