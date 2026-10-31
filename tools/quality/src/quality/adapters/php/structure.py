"""Structure metrics of PHP files, measured by the helper with the pinned nikic/php-parser.

The helper applies the counting rules of decision 0007 and resolves imports:
a `use` declaration or a fully qualified class name through the PSR-4
prefixes of the component's composer.json, and an `include` or `require` of a
literal path, absolute or below `__DIR__`. A name or path that resolves to
another file of the snapshot becomes a dependency edge, which the engine
restricts to the component's source files; vendor classes resolve to nothing.

The runner needs the same answers: which files only declare and can be loaded
before the tests, and how many functions and methods each file has, whose
branches Xdebug must measure. The helper therefore answers once per work
area, component, and file, and its answers are kept.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from quality.adapters.helperio import Entry, repository_path
from quality.adapters.native import fail
from quality.adapters.php.config import Prefix
from quality.adapters.php.helper import FIELDS, TOOL, Helper
from quality.adapters.php.runtime import Php
from quality.adapters.target import Target
from quality.boundedio import JsonValue
from quality.metrics import FileMetrics, FunctionMetrics, ImportRef, Measurements, StructureError


@dataclass(frozen=True, slots=True)
class PhpFile:
    """The helper's answer for one file.

    `loadable` says that the file only declares, so loading it runs nothing;
    `units` counts its named functions and the methods of its named classes.
    """

    path: str
    loadable: bool
    units: int
    metrics: FileMetrics | StructureError
    edges: Mapping[str, int]


def _function(entry: Entry) -> FunctionMetrics:
    return FunctionMetrics(
        name=FIELDS.text(entry, "name"),
        line=FIELDS.integer(entry, "line"),
        code_lines=FIELDS.integer(entry, "code_lines"),
        complexity=FIELDS.integer(entry, "complexity"),
        nesting=FIELDS.integer(entry, "nesting"),
        parameters=FIELDS.integer(entry, "parameters"),
    )


def php_file(target: Target, entry: Entry) -> PhpFile:
    """Read the helper's entry for one file."""
    path = target.relative(TOOL, FIELDS.text(entry, "file"))
    loadable, units = FIELDS.flag(entry, "loadable"), FIELDS.integer(entry, "units")
    error = FIELDS.optional_entry(entry, "error")
    if error is not None:
        problem = StructureError(
            path, FIELDS.optional_integer(error, "line"), FIELDS.text(error, "message")
        )
        return PhpFile(path, loadable, units, problem, {})
    imports: list[ImportRef] = []
    edges: dict[str, int] = {}
    for item in FIELDS.entries(entry, "imports"):
        line = FIELDS.integer(item, "line")
        imports.append(ImportRef(FIELDS.text(item, "specifier"), line))
        resolved = FIELDS.optional_text(item, "resolved")
        found = None if resolved is None else repository_path(target.snapshot, resolved)
        if found is not None and found != path:
            edges.setdefault(found, line)
    functions = tuple(_function(item) for item in FIELDS.entries(entry, "functions"))
    return PhpFile(path, loadable, units, FileMetrics(path, functions, tuple(imports)), edges)


def measurements(files: Iterable[PhpFile]) -> Measurements:
    """Collect the metrics, parse errors, and dependency edges of measured files."""
    measured: list[FileMetrics] = []
    errors: list[StructureError] = []
    dependencies: dict[str, dict[str, int]] = {}
    for file in files:
        if isinstance(file.metrics, StructureError):
            errors.append(file.metrics)
        else:
            measured.append(file.metrics)
            dependencies[file.path] = dict(file.edges)
    return Measurements(tuple(measured), tuple(errors), dependencies)


def autoload(directory: Path, prefixes: Iterable[Prefix]) -> list[JsonValue]:
    """Return the PSR-4 prefixes as the helper reads them, with absolute directories."""
    return [
        {
            "prefix": prefix.prefix,
            "directories": [str(directory / path) for path in prefix.directories],
        }
        for prefix in prefixes
    ]


@dataclass(slots=True)
class Structures:
    """The helper's answers per work area and component; each file is asked about once."""

    helper: Helper
    _known: dict[tuple[Path, str], dict[str, PhpFile]] = field(
        default_factory=dict[tuple[Path, str], dict[str, PhpFile]]
    )

    def files(
        self, target: Target, *, php: Php, work: Path, prefixes: Iterable[Prefix]
    ) -> list[PhpFile]:
        """Return the helper's answer for each file of `target`, asking only for new ones."""
        known = self._known.setdefault((work, target.component.id), {})
        missing = [path for path in target.files if path not in known]
        if missing:
            request = Target(target.component, target.snapshot, tuple(missing))
            response = self.helper.run(
                php,
                "structure",
                {
                    "autoload": autoload(target.directory, prefixes),
                    "files": [*request.arguments()],
                },
                work=work,
                cwd=target.directory,
            )
            for entry in FIELDS.entries(response, "files"):
                answer = php_file(target, entry)
                known[answer.path] = answer
        unanswered = [path for path in missing if path not in known]
        if unanswered:
            fail(TOOL, f"the structure answer lacks {', '.join(unanswered)}")
        return [known[path] for path in target.files]
