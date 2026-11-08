"""Resolve Python imports to files of the same component, without importing anything."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from pathlib import PurePosixPath

from quality.metrics import FileMetrics, ImportRef

PYTHON_SUFFIXES = (".py", ".pyi")


def import_roots(source_roots: Collection[str], files: Collection[str]) -> list[str]:
    """Return the directories that module names are relative to.

    A source root holding `__init__.py` is itself a package, so its parent is the
    import root (flat layout); otherwise the source root is (src layout).
    """
    roots: list[str] = []
    for root in source_roots:
        if f"{root}/__init__.py" in files:
            parent = str(PurePosixPath(root).parent)
            roots.append("" if parent == "." else parent)
        else:
            roots.append(root)
    return sorted(set(roots), key=len, reverse=True)


def module_of(path: str, roots: Collection[str]) -> tuple[str, bool] | None:
    """Return the dotted module name of a file and whether it is a package."""
    pure = PurePosixPath(path)
    if pure.suffix not in PYTHON_SUFFIXES:
        return None
    for root in roots:
        if root and not path.startswith(root + "/"):
            continue
        relative = PurePosixPath(path[len(root) + 1 :] if root else path).with_suffix("")
        parts = list(relative.parts)
        package = parts[-1] == "__init__"
        if package:
            parts.pop()
        if parts and all(part.isidentifier() for part in parts):
            return ".".join(parts), package
    return None


def targets(ref: ImportRef, module: str, *, package: bool) -> list[str]:
    """Return the absolute names an import can refer to; empty when it escapes the top."""
    target = ref.module
    if ref.level:
        base = module.split(".") if package else module.split(".")[:-1]
        if ref.level - 1 >= len(base):
            return []
        kept = base[: len(base) - (ref.level - 1)]
        target = ".".join([*kept, ref.module] if ref.module else kept)
    if ref.names:
        return [f"{target}.{name}" for name in ref.names]
    return [target]


def _longest_known(name: str, index: Mapping[str, str]) -> str | None:
    parts = name.split(".")
    while parts:
        found = index.get(".".join(parts))
        if found is not None:
            return found
        parts.pop()
    return None


def file_dependencies(
    metrics: Mapping[str, FileMetrics], roots: Collection[str]
) -> dict[str, dict[str, int]]:
    """Map each file to the files it imports, with the line of the first such import."""
    modules = {path: module_of(path, roots) for path in metrics}
    index = {found[0]: path for path, found in modules.items() if found is not None}
    edges: dict[str, dict[str, int]] = {}
    for path, file_metrics in metrics.items():
        found = modules[path]
        if found is not None:
            edges[path] = _imported_files(path, file_metrics, found, index)
    return edges


def _imported_files(
    path: str, file_metrics: FileMetrics, found: tuple[str, bool], index: Mapping[str, str]
) -> dict[str, int]:
    name, package = found
    out: dict[str, int] = {}
    for ref in file_metrics.imports:
        for target in targets(ref, name, package=package):
            resolved = _longest_known(target, index)
            if resolved is not None and resolved != path:
                out.setdefault(resolved, ref.line)
    return out
