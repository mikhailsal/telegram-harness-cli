import json

from quality.diagnostics import Diagnostic, Location, Outcome, Severity
from quality.diagnostics.render import diagnostic_to_dict, outcome_to_json, printable, render_text

LOCATED = Diagnostic(
    rule="size.file",
    severity=Severity.ERROR,
    message="file is too long",
    location=Location("src/a.py", 4, 1),
    component="app",
    hint="Split the file.",
    data={"z": "1", "a": "2"},
)
GLOBAL = Diagnostic(rule="repo.note", severity=Severity.WARNING, message="advisory")


def test_diagnostic_to_dict_is_flat_and_sorted() -> None:
    assert diagnostic_to_dict(LOCATED) == {
        "rule": "size.file",
        "severity": "error",
        "kind": "violation",
        "message": "file is too long",
        "path": "src/a.py",
        "line": 4,
        "column": 1,
        "component": "app",
        "hint": "Split the file.",
        "data": {"a": "2", "z": "1"},
    }
    assert list(diagnostic_to_dict(LOCATED)["data"]) == ["a", "z"]
    unlocated = diagnostic_to_dict(GLOBAL)
    assert (unlocated["path"], unlocated["line"], unlocated["column"]) == (None, None, None)


def test_outcome_json_has_summary_and_exit_code() -> None:
    document = json.loads(outcome_to_json(Outcome.of([LOCATED, GLOBAL]), command="check"))
    assert document["schema_version"] == 1
    assert document["command"] == "check"
    assert document["exit_code"] == 1
    assert document["summary"] == {"errors": 1, "warnings": 1, "total": 2}
    assert [d["rule"] for d in document["diagnostics"]] == ["repo.note", "size.file"]


def test_render_text_reports_location_hint_component_and_verdict() -> None:
    text = render_text(Outcome.of([LOCATED, GLOBAL]))
    assert text.splitlines() == [
        "warning: advisory (repo.note)",
        "src/a.py:4:1: error: file is too long (size.file) [app]",
        "  hint: Split the file.",
        "FAILED: 1 error(s), 1 warning(s)",
    ]


def test_render_text_of_empty_outcome_passes() -> None:
    assert render_text(Outcome.of([])) == "PASSED: 0 error(s), 0 warning(s)\n"


def test_printable_escapes_controls_and_undecodable_bytes() -> None:
    assert printable("a\x1b[1mb\tc\udcffd\x85e\ud800f\x7fé") == (
        "a\\x1b[1mb\\x09c\\xffd\\x85e\\ud800f\\x7fé"
    )


def test_text_output_cannot_drive_the_terminal() -> None:
    diagnostic = Diagnostic(
        rule="x.y",
        severity=Severity.ERROR,
        message="bad\x1b]0;title\x07",
        location=Location("a\nb"),
        hint="h\r",
    )
    assert render_text(Outcome.of([diagnostic])).splitlines()[:2] == [
        "a\\x0ab: error: bad\\x1b]0;title\\x07 (x.y)",
        "  hint: h\\x0d",
    ]


def test_json_output_is_ascii_and_accepts_extra_sections() -> None:
    text = outcome_to_json(Outcome.of([]), command="inventory", extra={"files": ["é\udcff"]})
    assert text.isascii()
    assert json.loads(text)["files"] == ["é\udcff"]
