"""Cobertura XML from normalized coverage, for CI systems that display it."""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement, tostring

from quality.coverage import Counter, CoverageReport, total


def _rate(counter: Counter) -> str:
    return "1" if not counter.applicable else f"{counter.covered / counter.total:.4f}"


def _counted(lines: Counter, branches: Counter) -> dict[str, str]:
    return {"line-rate": _rate(lines), "branch-rate": _rate(branches), "complexity": "0"}


def cobertura_document(component: str, report: CoverageReport) -> bytes:
    """Render a coverage report as one Cobertura package with a class per file.

    Rates are shown with four decimals; the exact counts are in the
    `lines-*` and `branches-*` attributes, and paths are repository-relative.
    """
    measured = [report.files[path] for path in sorted(report.files)]
    lines = total(entry.lines for entry in measured)
    branches = total(entry.branches for entry in measured if entry.branches_measured)
    root = Element(
        "coverage",
        {
            **_counted(lines, branches),
            "lines-valid": str(lines.total),
            "lines-covered": str(lines.covered),
            "branches-valid": str(branches.total),
            "branches-covered": str(branches.covered),
            "version": "quality",
            "timestamp": "0",
        },
    )
    SubElement(SubElement(root, "sources"), "source").text = "."
    package = SubElement(SubElement(root, "packages"), "package", {"name": component})
    package.attrib.update(_counted(lines, branches))
    classes = SubElement(package, "classes")
    for entry in measured:
        attributes = {"name": entry.path, "filename": entry.path}
        unit = SubElement(classes, "class", {**attributes, **_counted(entry.lines, entry.branches)})
        SubElement(unit, "methods")
        rows = SubElement(unit, "lines")
        for line in sorted(entry.executable):
            SubElement(rows, "line", {"number": str(line), "hits": str(int(line in entry.covered))})
    return tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"
