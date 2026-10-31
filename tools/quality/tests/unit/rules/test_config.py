import pytest

from quality.diagnostics import Location
from quality.policy import Category
from quality.rules.config import TOO_DEEP, check_config
from quality.rules.model import CandidateFile
from quality.testkit.candidates import candidate

DEEP = "[" * 10_000 + "]" * 10_000


def config(
    path: str, data: bytes | str | None, category: Category = Category.CONFIG
) -> CandidateFile:
    return candidate(path, data, category)


def findings(*files: CandidateFile) -> list[tuple[str, Location | None, str]]:
    return [(d.rule, d.location, d.message) for d in check_config(files)]


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("package.json", '{"name": "x", "nested": {"a": 1, "b": [1, 2]}}\n'),
        ("pyproject.toml", '[project]\nname = "x"\n'),
        ("ci.yaml", "a: 1\n---\nb: [1, 2]\n"),
        ("conf/app.yml", "key: value\n"),
        ("UPPER.JSON", "[]"),
    ],
)
def test_valid_configuration_passes(path: str, text: str) -> None:
    assert findings(config(path, text)) == []


@pytest.mark.parametrize(
    ("path", "text", "line", "message"),
    [
        ("a.json", '{\n  "a": 1,\n}\n', 2, "Illegal trailing comma before end of object"),
        ("a.json", '{"a": {"b": 1, "b": 2}}', None, "duplicate key 'b'"),
        ("a.toml", "a = 1\nb = \n", 2, "Invalid value"),
        ("a.toml", "a = 1\na = 2\n", 2, "Cannot overwrite a value"),
        ("a.yaml", "a: 1\n---\nb: [1\n", 4, "expected ',' or ']', but got '<stream end>'"),
        ("a.yml", "key: value\n  bad: indent\n", 2, "mapping values are not allowed here"),
        ("a.json", '{"a": NaN}', None, "non-standard JSON constant NaN"),
        ("a.json", DEEP, None, "containers nest deeper than 512 levels"),
        ("a.toml", f"a = {DEEP}", None, TOO_DEEP),
        ("a.yaml", DEEP, None, TOO_DEEP),
    ],
)
def test_syntax_errors_are_reported_at_their_line(
    path: str, text: str, line: int | None, message: str
) -> None:
    assert findings(config(path, text)) == [("config.syntax", Location(path, line), message)]


def test_unmarked_yaml_errors_have_no_line() -> None:
    [(rule, location, message)] = findings(config("a.yaml", "a: \x07\n"))
    assert (rule, location) == ("config.syntax", Location("a.yaml"))
    assert message.startswith("unacceptable character #x0007")


def test_component_is_reported() -> None:
    file = candidate("sub/a.json", "{", Category.CONFIG, component="web")
    assert [d.component for d in check_config([file])] == ["web"]


@pytest.mark.parametrize(
    "path",
    [
        "tsconfig.json",
        "web/tsconfig.build.json",
        "jsconfig.json",
        ".eslintrc.json",
        ".vscode/settings.json",
        "app/.devcontainer/devcontainer.json",
    ],
)
def test_json_with_comments_is_left_to_its_tools(path: str) -> None:
    assert findings(config(path, '{\n  // comment\n  "a": 1,\n}\n')) == []


@pytest.mark.parametrize(
    "file",
    [
        config("setup.cfg", "[broken"),
        config("docs/data.json", "{", Category.DOCS),
        config("vendor/x.json", "{", Category.VENDOR),
        config("a.json", b"\xff{"),
        config("a.json", None),
        config("notjson.json5", "{"),
    ],
    ids=["unknown-suffix", "docs", "vendor", "not-utf8", "symlink", "json5"],
)
def test_other_files_are_not_parsed(file: CandidateFile) -> None:
    assert findings(file) == []
