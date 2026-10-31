from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.target import Target, document, findings, location, number
from quality.boundedio import JsonValue
from quality.diagnostics import Location
from quality.process import Completed
from quality.testkit.policies import ComponentSpec, build_policy

(APP,) = build_policy(ComponentSpec(root="app")).components


def completed(stdout: bytes) -> Completed:
    return Completed(("tool",), 0, stdout, b"", 0.0)


def test_files_are_named_absolutely(tmp_path: Path) -> None:
    target = Target(APP, tmp_path, ("app/src/a.py", "app/tests/test_a.py"))
    assert target.directory == tmp_path / "app"
    assert target.arguments() == (
        str(tmp_path / "app/src/a.py"),
        str(tmp_path / "app/tests/test_a.py"),
    )


def test_report_names_map_back_to_repository_paths(tmp_path: Path) -> None:
    target = Target(APP, tmp_path, ())
    assert target.relative("ruff", str(tmp_path / "app/src/a.py")) == "app/src/a.py"
    assert target.relative("ruff", "src/./b.py") == "app/src/b.py"
    assert target.relative("ruff", "../other/c.py") == "other/c.py"


def test_report_names_through_a_linked_snapshot(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    target = Target(APP, link, ())
    assert target.relative("pyright", str(real / "app" / "a.py")) == "app/a.py"
    assert target.relative("pyright", str(link / "app" / "a.py")) == "app/a.py"


@pytest.mark.parametrize(
    ("filename", "message"),
    [
        (None, "ruff: report entry without a file name"),
        ("", "ruff: report entry without a file name"),
        (7, "ruff: report entry without a file name"),
        ("/etc/passwd", "ruff: report names a file outside the snapshot: /etc/passwd"),
        ("../../x.py", "ruff: report names a file outside the snapshot: ../../x.py"),
    ],
)
def test_unusable_report_names_fail(tmp_path: Path, filename: JsonValue, message: str) -> None:
    target = Target(APP, tmp_path / "snapshot", ())
    with pytest.raises(ToolFailedError) as raised:
        target.relative("ruff", filename)
    assert raised.value.diagnostic.message == message


def test_tool_documents_must_be_json() -> None:
    assert document("ruff", completed(b'[{"a": 1}]')) == [{"a": 1}]
    with pytest.raises(ToolFailedError, match=r"^ruff: malformed JSON in ruff output"):
        document("ruff", completed(b"error: no such file"))


@pytest.mark.parametrize("value", [{"a": 1}, [1], [{"a": 1}, "b"], None])
def test_findings_must_be_a_list_of_objects(value: JsonValue) -> None:
    with pytest.raises(ToolFailedError, match="output is not a list of findings"):
        findings("ruff", value)


def test_findings_are_returned() -> None:
    assert findings("ruff", [{"a": 1}, {}]) == [{"a": 1}, {}]
    assert findings("ruff", []) == []


def test_numbers_are_non_negative_integers() -> None:
    entry: dict[str, JsonValue] = {"row": 3, "zero": 0, "neg": -1, "flag": True, "text": "4"}
    assert [number(entry, key) for key in entry] == [3, 0, None, None, None]
    assert number(entry, "missing") is None


def test_locations_drop_a_column_without_a_line() -> None:
    assert location("a.py", 2, 5) == Location("a.py", 2, 5)
    assert location("a.py", 2, None) == Location("a.py", 2, None)
    assert location("a.py", None, 5) == Location("a.py", None, None)
