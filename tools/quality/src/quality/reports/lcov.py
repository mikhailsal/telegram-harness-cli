"""LCOV from normalized coverage: which executable lines ran, per repository path."""

from __future__ import annotations

from quality.coverage import CoverageReport


def lcov_document(report: CoverageReport) -> str:
    """Render the line coverage of a report as an LCOV tracefile.

    The normalized model keeps whether a line ran, not how often, so a covered
    line has one hit. It keeps branch totals but not the branches themselves,
    which LCOV records one by one, so branches are left to the Cobertura
    export and the native reports.
    """
    lines: list[str] = []
    for path in sorted(report.files):
        measured = report.files[path]
        lines.extend(("TN:", f"SF:{path}"))
        lines.extend(
            f"DA:{line},{int(line in measured.covered)}" for line in sorted(measured.executable)
        )
        lines.extend((f"LF:{measured.lines.total}", f"LH:{measured.lines.covered}"))
        lines.append("end_of_record")
    return "".join(f"{line}\n" for line in lines)
