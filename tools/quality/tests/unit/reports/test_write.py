from pathlib import Path

import pytest

from quality.coverage import CoverageReport, FileCoverage
from quality.reports import Evidence, ReportsError, prepare_reports, write_evidence
from quality.testing import CaseResult, Outcome, Suite, SuiteResults

RESULTS = SuiteResults((CaseResult("tests::ok", Outcome.PASSED),))
COVERAGE = CoverageReport(
    {"src/a.py": FileCoverage("src/a.py", frozenset({1}), frozenset({1}))}, branches_measured=True
)


def test_the_reports_directory_must_be_new_or_empty(tmp_path: Path) -> None:
    assert prepare_reports(tmp_path / "new" / "reports") == (tmp_path / "new" / "reports")
    (tmp_path / "empty").mkdir()
    assert prepare_reports(tmp_path / "empty") == tmp_path / "empty"
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "report.json").write_text("{}")
    (tmp_path / "file").write_text("")
    (tmp_path / "link").symlink_to(tmp_path / "empty")
    for refused in ("old", "file", "link"):
        with pytest.raises(ReportsError, match="must be new or empty"):
            prepare_reports(tmp_path / refused)


def test_exports_and_native_reports_are_written_with_relative_paths(tmp_path: Path) -> None:
    root, reports = tmp_path / "snapshot", tmp_path / "reports"
    output = tmp_path / "work" / "out"
    output.mkdir(parents=True)
    native = output / "coverage.json"
    native.write_text(f'{{"files": {{"{root}/src/a.py": {{}}, "{root.resolve()}/b": 1}}}}')
    evidence = [
        Evidence("app", Suite.UNIT, RESULTS, COVERAGE, (native,)),
        Evidence("app", Suite.E2E, None, None, (output / "missing.xml",)),
    ]
    found = write_evidence(reports, root, evidence)
    assert [(d.rule, d.component) for d in found] == [("reports.incomplete", "app")]
    assert "e2e report 'missing.xml' was not copied" in found[0].message
    written = sorted(
        str(path.relative_to(reports)) for path in reports.rglob("*") if path.is_file()
    )
    assert written == [
        "coverage/app/unit.cobertura.xml",
        "coverage/app/unit.lcov",
        "junit/app/unit.xml",
        "native/app/unit/coverage.json",
    ]
    copied = (reports / "native/app/unit/coverage.json").read_text()
    assert copied == '{"files": {"src/a.py": {}, "b": 1}}'
