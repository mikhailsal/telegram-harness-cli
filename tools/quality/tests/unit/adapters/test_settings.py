from pathlib import Path

import pytest

from quality.adapters.settings import (
    CONFIG_LIMIT,
    inside,
    label,
    load_json_object,
    load_toml,
    load_yaml_mapping,
    strings,
    table,
)
from quality.boundedio import ReportError


def test_toml_files_are_parsed(tmp_path: Path) -> None:
    path = tmp_path / "a.toml"
    path.write_text("[tool.x]\nvalue = 1\n")
    assert load_toml(path) == {"tool": {"x": {"value": 1}}}


@pytest.mark.parametrize(
    "data", [b"[tool\n", b"key = '\xff'\n", b"a = " + b"[" * 10_000 + b"]" * 10_000]
)
def test_unusable_toml_is_reported(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "a.toml"
    path.write_bytes(data)
    with pytest.raises(ReportError, match=r"^a\.toml is not valid TOML: "):
        load_toml(path)


def test_oversized_and_missing_files_are_reported(tmp_path: Path) -> None:
    big = tmp_path / "big.toml"
    big.write_bytes(b"#" * (CONFIG_LIMIT + 1))
    with pytest.raises(ReportError, match="exceeds"):
        load_toml(big)
    with pytest.raises(ReportError, match="not a regular file"):
        load_json_object(tmp_path / "missing.json")


def test_json_objects_are_parsed(tmp_path: Path) -> None:
    path = tmp_path / "a.json"
    path.write_text('{"a": [1, 2]}')
    assert load_json_object(path) == {"a": [1, 2]}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('{"a": 1, // comment\n}', "is not plain JSON"),
        ("[" * 100_000 + "]" * 100_000, "is not plain JSON: containers nest deeper than 512"),
        ('{"a": NaN}', "is not plain JSON: non-standard JSON constant NaN"),
        ("[1, 2]", "does not hold a JSON object"),
    ],
)
def test_unusable_json_is_reported(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "a.json"
    path.write_text(text)
    with pytest.raises(ReportError, match=f"^a.json {message}"):
        load_json_object(path)


def test_yaml_mappings_are_parsed(tmp_path: Path) -> None:
    path = tmp_path / "a.yaml"
    path.write_text("base: &base {x: 1}\nuse: *base\nmore: [*base, *base]\n")
    assert load_yaml_mapping(path) == {"base": {"x": 1}, "use": {"x": 1}, "more": [{"x": 1}] * 2}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"{not yaml", "is not valid YAML: "),
        (b"a: '\xff'\n", "is not valid YAML: "),
        (b"a: 1\na: 2\n", "repeats the key 'a'"),
        (b"x:\n  - {k: 1, j: 2, k: 3}\n", "repeats the key 'k'"),
        (b"- a\n", "does not hold a YAML mapping"),
        (b"", "does not hold a YAML mapping"),
    ],
)
def test_unusable_yaml_is_reported(tmp_path: Path, data: bytes, message: str) -> None:
    path = tmp_path / "a.yaml"
    path.write_bytes(data)
    with pytest.raises(ReportError, match=rf"^a\.yaml {message}"):
        load_yaml_mapping(path)


def test_nested_tables() -> None:
    data = {"tool": {"ruff": {"lint": {"select": ["E"]}}, "flag": 1}}
    assert table(data, "tool", "ruff", "lint") == {"select": ["E"]}
    assert table(data, "tool", "missing") is None
    assert table(data, "tool", "flag") is None
    assert table(data) == data


def test_strings_of_a_list_setting() -> None:
    assert strings(["a", 1, "b", None]) == ["a", "b"]
    assert strings("a") == []
    assert strings(None) == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("src", True),
        ("../lib", True),
        ("../..", False),
        ("/etc", False),
        ("~/stubs", False),
        ("$HOME/stubs", False),
        ("${HOME}", False),
    ],
)
def test_paths_inside_the_snapshot(tmp_path: Path, value: str, expected: bool) -> None:
    base = tmp_path / "snapshot" / "app"
    base.mkdir(parents=True)
    assert inside(base, value, tmp_path / "snapshot") is expected


def test_links_out_of_the_snapshot_are_outside(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "escape").symlink_to(tmp_path)
    assert not inside(snapshot, "escape/x", snapshot)


def test_labels_are_relative_to_the_component(tmp_path: Path) -> None:
    directory = tmp_path / "app"
    directory.mkdir()
    assert label(directory / "pyproject.toml", directory) == "pyproject.toml"
    assert label(tmp_path / "shared" / "ruff.toml", directory) == "../shared/ruff.toml"
