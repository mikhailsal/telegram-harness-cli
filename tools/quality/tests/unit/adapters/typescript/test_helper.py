import json
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.typescript import helper
from quality.adapters.typescript.environment import Node
from quality.boundedio import JsonValue
from quality.testkit.nodefakes import script


def fake_helper(tmp_path: Path, response: str, code: int = 0) -> Path:
    """Write a helper that saves its command and request, prints `response`, and exits."""
    (tmp_path / "response.json").write_text(response)
    body = (
        f"echo \"$1\" > '{tmp_path / 'command'}'\ncp \"$2\" '{tmp_path / 'request.json'}'\n"
        f"cat '{tmp_path / 'response.json'}'\necho 'helper stderr' >&2\nexit {code}\n"
    )
    return script(tmp_path / "main.ts", body)


def test_the_helper_lives_beside_the_package() -> None:
    assert helper.HELPER.parts[-4:] == ("helpers", "typescript", "src", "main.ts")
    assert (helper.CORE / "src" / "quality" / "adapters").is_dir()


def test_a_request_is_answered(node: Node, tmp_path: Path) -> None:
    program = fake_helper(tmp_path, '{"protocol": 1, "files": []}')
    request: dict[str, JsonValue] = {"files": ["a.ts"]}
    work = tmp_path / "work"
    response = helper.run_helper(
        helper.Program(node, program), "structure", request, work=work, cwd=tmp_path
    )
    assert response == {"protocol": 1, "files": []}
    assert (tmp_path / "command").read_text() == "structure\n"
    assert json.loads((tmp_path / "request.json").read_text()) == {"protocol": 1, "files": ["a.ts"]}
    assert [path.name[:10] for path in (work / "typescript-requests").iterdir()] == ["structure-"]


@pytest.mark.parametrize(
    ("response", "code", "message"),
    [
        ('{"protocol": 1}', 1, "exit status 1: helper stderr"),
        ('{"protocol": 1}', 2, "exit status 2: helper stderr"),
        ("not json", 0, "typescript helper types output"),
        ("[1]", 0, "types answered without protocol 1"),
        ('{"protocol": 2}', 0, "types answered without protocol 1"),
    ],
)
def test_unusable_answers_are_tool_failures(
    node: Node, tmp_path: Path, response: str, code: int, message: str
) -> None:
    program = fake_helper(tmp_path, response, code)
    with pytest.raises(ToolFailedError, match=message):
        helper.run_helper(helper.Program(node, program), "types", {}, work=tmp_path, cwd=tmp_path)


def test_a_missing_helper_is_a_tool_failure(node: Node, tmp_path: Path) -> None:
    missing = tmp_path / "missing.ts"
    with pytest.raises(ToolFailedError, match="the helper is missing at"):
        helper.run_helper(helper.Program(node, missing), "types", {}, work=tmp_path, cwd=tmp_path)


def test_absolute_names_become_repository_paths(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "app").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    assert helper.repository_path(link, f"{link}/app/./a.ts") == "app/a.ts"
    assert helper.repository_path(link, f"{real}/app/b.ts") == "app/b.ts"
    assert helper.repository_path(link, str(link)) is None
    assert helper.repository_path(link, f"{tmp_path}/other/c.ts") is None
    assert helper.repository_path(link, "app/relative.ts") is None


def test_fields_are_read_by_type() -> None:
    entry: dict[str, JsonValue] = {
        "items": [{"a": 1}],
        "name": "x",
        "names": ["a", "b"],
        "count": 3,
        "zero": 0,
        "none": None,
        "on": True,
        "object": {"k": "v"},
    }
    assert helper.entries(entry, "items") == [{"a": 1}]
    assert helper.text(entry, "name") == "x"
    assert helper.texts(entry, "names") == ["a", "b"]
    assert helper.integer(entry, "count") == 3
    assert helper.optional_integer(entry, "count") == 3
    assert helper.optional_integer(entry, "zero") is None
    assert helper.optional_integer(entry, "none") is None
    assert helper.optional_text(entry, "none") is None
    assert helper.optional_text(entry, "name") == "x"
    assert helper.flag(entry, "on") is True
    assert helper.optional_entry(entry, "object") == {"k": "v"}
    assert helper.optional_entry(entry, "missing") is None


@pytest.mark.parametrize(
    ("read", "value", "message"),
    [
        (helper.entries, [1], "'field' is not a list of objects"),
        (helper.entries, "x", "'field' is not a list of objects"),
        (helper.text, 1, "'field' is not a string"),
        (helper.texts, [1], "'field' is not a list of strings"),
        (helper.texts, None, "'field' is not a list of strings"),
        (helper.integer, -1, "'field' is not a non-negative integer"),
        (helper.integer, True, "'field' is not a non-negative integer"),
        (helper.integer, "1", "'field' is not a non-negative integer"),
        (helper.flag, 1, "'field' is not a boolean"),
        (helper.optional_entry, [], "'field' is not an object"),
    ],
)
def test_fields_of_the_wrong_type_are_tool_failures(
    read: object, value: JsonValue, message: str
) -> None:
    assert callable(read)
    with pytest.raises(ToolFailedError, match=message):
        read({"field": value}, "field")
