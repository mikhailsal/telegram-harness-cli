"""Write a gate run's reports into a new or empty directory, with relative locations."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from quality.boundedio import ReportError, read_bounded
from quality.diagnostics import Diagnostic, Rule, Severity
from quality.reports.cobertura import cobertura_document
from quality.reports.junit import junit_document
from quality.reports.lcov import lcov_document
from quality.reports.model import Evidence

INCOMPLETE = Rule(
    id="reports.incomplete",
    title="A native report could not be copied into the reports directory",
    requirement="R09",
    explanation=(
        "Reports are a record of the run, never its verdict. A native report that "
        "disappeared or exceeds the read limit is not copied, and this warning says so, "
        "so a missing artifact is visible rather than silently absent."
    ),
    remedy="Check the disk space and the size of the runner's reports, then run the gate again.",
    severity=Severity.WARNING,
)
RULES = (INCOMPLETE,)


class ReportsError(Exception):
    """The reports directory cannot hold the reports of a fresh run."""


def prepare_reports(directory: Path) -> Path:
    """Create the reports directory, or accept an empty one; earlier reports are refused."""
    if directory.is_dir() and not directory.is_symlink() and not any(directory.iterdir()):
        return directory.resolve()
    if directory.exists() or directory.is_symlink():
        msg = f"reports directory '{directory}' must be new or empty"
        raise ReportsError(msg)
    directory.mkdir(parents=True)
    return directory.resolve()


def _relative(data: bytes, root: Path) -> bytes:
    """Strip the snapshot location from a native report so its paths are relative."""
    for base in dict.fromkeys((root.as_posix(), root.resolve().as_posix())):
        data = data.replace(base.encode() + b"/", b"")
    return data


def _write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def _native(run: Evidence, directory: Path, root: Path) -> list[Diagnostic]:
    found: list[Diagnostic] = []
    for path in run.native:
        try:
            data = read_bounded(path)
        except ReportError as error:
            message = f"{run.suite} report '{path.name}' was not copied: {error}"
            found.append(INCOMPLETE.diagnostic(message, component=run.component))
            continue
        _write(
            directory / "native" / run.component / str(run.suite) / path.name, _relative(data, root)
        )
    return found


def write_evidence(directory: Path, root: Path, evidence: Iterable[Evidence]) -> list[Diagnostic]:
    """Write the exports and native copies of each run while the snapshot `root` exists.

    Exports are written only from valid results and coverage; a native report
    that cannot be copied is a warning.
    """
    found: list[Diagnostic] = []
    for run in evidence:
        name = f"{run.component}/{run.suite}"
        if run.results is not None:
            _write(
                directory / "junit" / f"{name}.xml",
                junit_document(run.component, run.suite, run.results),
            )
        if run.coverage is not None:
            _write(directory / "coverage" / f"{name}.lcov", lcov_document(run.coverage).encode())
            cobertura = cobertura_document(run.component, run.coverage)
            _write(directory / "coverage" / f"{name}.cobertura.xml", cobertura)
        found.extend(_native(run, directory, root))
    return found
