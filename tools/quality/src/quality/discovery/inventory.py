"""Build the inventory of a materialized candidate: every file classified and owned."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from quality.diagnostics import Diagnostic, Location
from quality.discovery import rules
from quality.discovery.classify import Classified, FileRecord, Region, classify, matching
from quality.discovery.filetypes import HEAD_BYTES, FileKind, FileType, detect
from quality.discovery.manifests import (
    MAX_MANIFEST_BYTES,
    Manifest,
    ManifestError,
    is_manifest,
    parse_manifest,
)
from quality.gitio import TreeEntry
from quality.policy import Category, Component, Policy


@dataclass(frozen=True, slots=True)
class Inventory:
    """Classified candidate files, parsed manifests, and discovery findings."""

    files: tuple[FileRecord, ...]
    manifests: tuple[Manifest, ...]
    diagnostics: tuple[Diagnostic, ...]

    def of_component(self, component_id: str) -> tuple[FileRecord, ...]:
        """Return the files owned by one component."""
        return tuple(record for record in self.files if record.component == component_id)

    def in_category(self, category: Category) -> tuple[FileRecord, ...]:
        """Return the files of one category."""
        return tuple(record for record in self.files if record.category is category)


def _read(root: Path, path: str, limit: int) -> bytes:
    descriptor = os.open(root / path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read(limit)


def _classify_entry(policy: Policy, root: Path, entry: TreeEntry) -> Classified:
    if entry.is_gitlink:
        # A submodule is opaque: only an explicit vendor classification admits it.
        found = classify(policy, entry.path, FileType(FileKind.ASSET))
        if found.record.category is not Category.VENDOR:
            message = "submodule is not classified as vendor content"
            found.diagnostics.append(
                rules.SUBMODULE.diagnostic(message, location=Location(entry.path))
            )
        return found
    head = b"" if entry.is_symlink else _read(root, entry.path, HEAD_BYTES)
    return classify(policy, entry.path, detect(entry.path, head), head)


def _component_at(policy: Policy, directory: str) -> Component | None:
    return next((c for c in policy.components if c.root == directory), None)


def _manifest_findings(policy: Policy, manifest: Manifest) -> Iterator[Diagnostic]:
    location = Location(manifest.path)
    component = _component_at(policy, manifest.directory)
    if component is not None:
        if manifest.package and manifest.profile is not component.profile:
            message = f"{manifest.profile} manifest in {component.profile} component"
            yield rules.PROFILE_MISMATCH.diagnostic(
                message, location=location, component=component.id
            )
    elif manifest.package and not manifest.workspace:
        message = f"{manifest.profile} package at '{manifest.directory}' has no component"
        yield rules.MANIFEST_OUTSIDE_COMPONENT.diagnostic(message, location=location)


def _manifests(
    policy: Policy, root: Path, records: Iterable[FileRecord]
) -> tuple[list[Manifest], list[Diagnostic]]:
    manifests: list[Manifest] = []
    found: list[Diagnostic] = []
    for record in records:
        if record.region is Region.TESTS or record.explicit or not is_manifest(record.path):
            continue
        try:
            data = _read(root, record.path, MAX_MANIFEST_BYTES + 1)
            manifest = parse_manifest(record.path, data)
        except (ManifestError, OSError) as exc:
            location = Location(record.path)
            found.append(rules.MANIFEST_INVALID.diagnostic(str(exc), location=location))
            continue
        manifests.append(manifest)
        found.extend(_manifest_findings(policy, manifest))
    return manifests, found


def _prefixes(paths: Iterable[str]) -> set[str]:
    prefixes: set[str] = set()
    for path in paths:
        parts = path.split("/")
        prefixes.update("/".join(parts[: index + 1]) for index in range(len(parts)))
    return prefixes


def _missing_roots(policy: Policy, paths: Iterable[str]) -> Iterator[Diagnostic]:
    present = _prefixes(paths)
    for component in policy.components:
        prefix = "" if component.root == "." else component.root + "/"
        declared = [component.root, *(prefix + p for p in component.source)]
        declared.extend(prefix + p for p in component.tests.all_paths())
        for path in declared:
            if path != "." and path not in present:
                message = f"declared root '{path}' has no tracked files"
                yield rules.MISSING_ROOT.diagnostic(
                    message, location=Location("quality.toml"), component=component.id
                )


def _unused(policy: Policy, used: set[str]) -> Iterator[Diagnostic]:
    for entry in policy.classification:
        if entry.pattern not in used:
            message = f"classification pattern '{entry.pattern}' matches no file"
            yield rules.UNUSED_CLASSIFICATION.diagnostic(message, location=Location("quality.toml"))


def build_inventory(policy: Policy, root: Path, entries: Sequence[TreeEntry]) -> Inventory:
    """Classify every entry of a snapshot rooted at `root` and check component ownership."""
    records: list[FileRecord] = []
    diagnostics: list[Diagnostic] = []
    used: set[str] = set()
    for entry in entries:
        found = _classify_entry(policy, root, entry)
        records.append(found.record)
        diagnostics.extend(found.diagnostics)
        used.update(found.patterns)
    manifests, manifest_findings = _manifests(policy, root, records)
    diagnostics.extend(manifest_findings)
    diagnostics.extend(_missing_roots(policy, (entry.path for entry in entries)))
    diagnostics.extend(_unused(policy, used))
    return Inventory(tuple(records), tuple(manifests), tuple(diagnostics))


__all__ = ["Inventory", "build_inventory", "matching"]
