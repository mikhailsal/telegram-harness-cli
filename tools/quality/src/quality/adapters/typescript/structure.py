"""Structure metrics of TypeScript files, measured by the helper with the TypeScript parser.

The helper applies the counting rules of decision 0007 and resolves each import
with the module resolution of the project that owns the file, so `paths`
aliases are followed. Imports of packages, workspace packages included, resolve
to nothing; an import that resolves to another file of the snapshot becomes a
dependency edge, which the engine restricts to the component's source files.
"""

from __future__ import annotations

from pathlib import Path

from quality.adapters.target import Target
from quality.adapters.typescript import helper
from quality.adapters.typescript.config import TSCONFIG
from quality.adapters.typescript.environment import Node
from quality.boundedio import JsonValue
from quality.metrics import FileMetrics, FunctionMetrics, ImportRef, Measurements, StructureError


def _function(entry: helper.Entry) -> FunctionMetrics:
    return FunctionMetrics(
        name=helper.text(entry, "name"),
        line=helper.integer(entry, "line"),
        code_lines=helper.integer(entry, "code_lines"),
        complexity=helper.integer(entry, "complexity"),
        nesting=helper.integer(entry, "nesting"),
        parameters=helper.integer(entry, "parameters"),
    )


def _imports(
    snapshot: Path, entries: list[helper.Entry]
) -> tuple[tuple[ImportRef, ...], dict[str, int]]:
    imports: list[ImportRef] = []
    edges: dict[str, int] = {}
    for entry in entries:
        line = helper.integer(entry, "line")
        imports.append(ImportRef(helper.text(entry, "specifier"), line))
        resolved = helper.optional_text(entry, "resolved")
        target = None if resolved is None else helper.repository_path(snapshot, resolved)
        if target is not None:
            edges.setdefault(target, line)
    return tuple(imports), edges


def files_request(target: Target) -> dict[str, JsonValue]:
    """Return the helper request that analyzes the named files with the component's tsconfig."""
    tsconfig = target.directory / TSCONFIG
    return {
        "typescript": str(target.directory),
        "tsconfig": str(tsconfig) if tsconfig.is_file() else None,
        "files": [*target.arguments()],
    }


def measure_files(
    target: Target, *, node: Node, work: Path, program: Path = helper.HELPER
) -> Measurements:
    """Measure the functions and imports of the named files."""
    request = files_request(target)
    response = helper.run_helper(
        helper.Program(node, program), "structure", request, work=work, cwd=target.directory
    )
    files: list[FileMetrics] = []
    errors: list[StructureError] = []
    dependencies: dict[str, dict[str, int]] = {}
    for entry in helper.entries(response, "files"):
        path = target.relative(helper.TOOL, helper.text(entry, "file"))
        error = helper.optional_entry(entry, "error")
        if error is not None:
            line = helper.optional_integer(error, "line")
            errors.append(StructureError(path, line, helper.text(error, "message")))
            continue
        functions = tuple(_function(item) for item in helper.entries(entry, "functions"))
        imports, edges = _imports(target.snapshot, helper.entries(entry, "imports"))
        files.append(FileMetrics(path, functions, imports))
        dependencies[path] = edges
    return Measurements(tuple(files), tuple(errors), dependencies)
