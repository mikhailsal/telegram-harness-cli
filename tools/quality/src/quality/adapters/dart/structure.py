"""Structure metrics of Dart files, measured by the helper with the pinned package:analyzer.

The helper applies the counting rules of decision 0007 and resolves each
import, export, and part directive: a `package:` URI of the component's own
package becomes a file under its `lib/`, a relative URI a file of the
component, and other packages and `dart:` libraries nothing. A directive that
resolves to another file of the snapshot becomes a dependency edge, which the
engine restricts to the component's source files.

The runner needs the same answers to tell part files, which the generated
coverage test must not import, from libraries. The helper therefore answers
once per work area, component, and file, and its answers are kept.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from quality.adapters.dart.environment import Sdk
from quality.adapters.dart.helper import FIELDS, TOOL, Helper
from quality.adapters.helperio import Entry, repository_path
from quality.adapters.native import fail
from quality.adapters.target import Target
from quality.metrics import FileMetrics, FunctionMetrics, ImportRef, Measurements, StructureError


@dataclass(frozen=True, slots=True)
class DartFile:
    """The helper's answer for one file: a part or a library, and its metrics or parse error."""

    path: str
    part: bool
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


def dart_file(target: Target, entry: Entry) -> DartFile:
    """Read the helper's entry for one file."""
    path = target.relative(TOOL, FIELDS.text(entry, "file"))
    part = FIELDS.flag(entry, "part")
    error = FIELDS.optional_entry(entry, "error")
    if error is not None:
        problem = StructureError(
            path, FIELDS.optional_integer(error, "line"), FIELDS.text(error, "message")
        )
        return DartFile(path, part, problem, {})
    imports: list[ImportRef] = []
    edges: dict[str, int] = {}
    for item in FIELDS.entries(entry, "imports"):
        line = FIELDS.integer(item, "line")
        imports.append(ImportRef(FIELDS.text(item, "specifier"), line))
        resolved = FIELDS.optional_text(item, "resolved")
        found = None if resolved is None else repository_path(target.snapshot, resolved)
        if found is not None:
            edges.setdefault(found, line)
    functions = tuple(_function(item) for item in FIELDS.entries(entry, "functions"))
    return DartFile(path, part, FileMetrics(path, functions, tuple(imports)), edges)


def measurements(files: Iterable[DartFile]) -> Measurements:
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


@dataclass(slots=True)
class Structures:
    """The helper's answers per work area and component; each file is asked about once."""

    helper: Helper
    _known: dict[tuple[Path, str], dict[str, DartFile]] = field(
        default_factory=dict[tuple[Path, str], dict[str, DartFile]]
    )

    def files(self, target: Target, *, sdk: Sdk, work: Path, package: str) -> list[DartFile]:
        """Return the helper's answer for each file of `target`, asking only for new ones."""
        known = self._known.setdefault((work, target.component.id), {})
        missing = [path for path in target.files if path not in known]
        if missing:
            request = Target(target.component, target.snapshot, tuple(missing))
            response = self.helper.run(
                sdk,
                "structure",
                {
                    "package": {"name": package, "root": str(target.directory)},
                    "files": [*request.arguments()],
                },
                work=work,
                cwd=target.directory,
            )
            for entry in FIELDS.entries(response, "files"):
                answer = dart_file(target, entry)
                known[answer.path] = answer
        unanswered = [path for path in missing if path not in known]
        if unanswered:
            fail(TOOL, f"the structure answer lacks {', '.join(unanswered)}")
        return [known[path] for path in target.files]
