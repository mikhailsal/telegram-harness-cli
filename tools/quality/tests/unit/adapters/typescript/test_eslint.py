import json
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.target import Target
from quality.adapters.typescript.environment import Node
from quality.adapters.typescript.eslint import LINT, eslint_configuration, eslint_lint
from quality.diagnostics import Diagnostic
from quality.testkit.nodefakes import script, typescript_component

APP = typescript_component()
FILES = ("app/src/a.ts", "app/src/b.ts", "app/src/c.ts", "app/tests/d.test.ts")


def located(found: list[Diagnostic]) -> list[tuple[str | None, int | None, int | None, str]]:
    assert {d.rule for d in found} <= {LINT.id}
    return [
        (d.location.path, d.location.line, d.location.column, d.message)
        if d.location
        else (None, None, None, d.message)
        for d in found
    ]


def answer(tmp_path: Path, response: object, code: int = 0) -> Path:
    (tmp_path / "answer.json").write_text(json.dumps(response))
    body = (
        f"cp \"$2\" '{tmp_path / 'request.json'}'\ncat '{tmp_path / 'answer.json'}'\nexit {code}\n"
    )
    return script(tmp_path / f"tool-{code}.sh", body)


def test_files_without_a_typed_configuration_are_reported(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    directory = snapshot / "app"
    directory.mkdir()
    entry = {"configured": True, "ignored": False, "typed": True, "parser": "typescript-eslint"}
    files = [
        {**entry, "file": str(directory / "src/a.ts")},
        {**entry, "file": str(directory / "src/b.ts"), "configured": False, "ignored": True},
        {**entry, "file": str(directory / "src/c.ts"), "ignored": True},
        {**entry, "file": str(directory / "tests/d.test.ts"), "typed": False},
    ]
    helper = answer(tmp_path, {"protocol": 1, "files": files})
    target = Target(APP, snapshot, FILES)
    found = eslint_configuration(target, node=node, work=tmp_path / "work", program=helper)
    unlinted = "no ESLint configuration lints this file; every source and test file is linted"
    assert located(found) == [
        ("app/src/b.ts", None, None, unlinted),
        ("app/src/c.ts", None, None, unlinted),
        (
            "app/tests/d.test.ts",
            None,
            None,
            (
                "ESLint lints this file without type information; "
                "set parserOptions.projectService in eslint.config.*"
            ),
        ),
    ]
    request = json.loads((tmp_path / "request.json").read_text())
    assert request == {
        "protocol": 1,
        "eslint": str(directory),
        "cwd": str(directory),
        "files": [str(snapshot / path) for path in FILES],
        "rules": [],
    }


def test_required_rules_must_be_enabled_at_any_level(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    directory = snapshot / "app"
    directory.mkdir()
    entry = {"configured": True, "ignored": False, "typed": True, "parser": "typescript-eslint"}
    levels = {"a": [("x/one", 2), ("x/two", 1)], "b": [("x/one", 0), ("x/two", 2)], "c": []}
    files = [
        {
            **entry,
            "file": str(directory / f"src/{name}.ts"),
            "rules": [{"rule": rule, "severity": level} for rule, level in rules],
        }
        for name, rules in levels.items()
    ]
    helper = answer(tmp_path, {"protocol": 1, "files": files})
    target = Target(APP, snapshot, FILES[:3])
    required = {"app/src/a.ts": ("x/one", "x/two"), "app/src/b.ts": ("x/two", "x/one")}
    found = eslint_configuration(
        target, node=node, work=tmp_path / "work", program=helper, required=required
    )
    assert located(found) == [
        (
            "app/src/b.ts",
            None,
            None,
            "the browser target requires x/one, which ESLint does not enable here",
        )
    ]
    request = json.loads((tmp_path / "request.json").read_text())
    assert request["rules"] == ["x/one", "x/two"]


def test_every_message_is_an_error(node: Node, snapshot: Path, tmp_path: Path) -> None:
    directory = snapshot / "app"
    directory.mkdir()
    results: list[dict[str, object]] = [
        {
            "filePath": str(directory / "src/a.ts"),
            "messages": [
                {"ruleId": "no-var", "message": "Unexpected var.", "line": 2, "column": 1},
                {"ruleId": None, "message": " Parsing error: ')' expected. ", "line": 4},
                {"ruleId": "", "message": "Unused eslint-disable directive.", "column": 3},
            ],
        },
        {"filePath": str(directory / "src/b.ts"), "messages": []},
    ]
    eslint = answer(tmp_path, results, 1)
    target = Target(APP, snapshot, FILES[:2])
    found = eslint_lint(target, node=node, script=eslint, work=tmp_path / "work")
    assert located(found) == [
        ("app/src/a.ts", 2, 1, "no-var: Unexpected var."),
        ("app/src/a.ts", 4, None, "Parsing error: ')' expected."),
        ("app/src/a.ts", None, None, "Unused eslint-disable directive."),
    ]
    assert (tmp_path / "work" / "eslint-suppressions.json").read_text() == "{}"


def test_options_disable_ignores_and_bulk_suppressions(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    (snapshot / "app").mkdir()
    eslint = script(tmp_path / "eslint.sh", f"echo \"$@\" > '{tmp_path / 'argv'}'\necho '[]'\n")
    target = Target(APP, snapshot, FILES[:1])
    assert eslint_lint(target, node=node, script=eslint, work=tmp_path / "work") == []
    argv = (tmp_path / "argv").read_text().split()
    suppressions = str(tmp_path / "work" / "eslint-suppressions.json")
    assert argv == [
        "--format",
        "json",
        "--no-ignore",
        "--report-unused-disable-directives-severity",
        "error",
        "--suppressions-location",
        suppressions,
        str(snapshot / FILES[0]),
    ]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("echo 'Oops! Something went wrong!' >&2\nexit 2\n", "exit status 2: Oops!"),
        ("echo 'not json'\nexit 1\n", "eslint output"),
        ("echo '{}'\n", "output is not a list of findings"),
        ('echo \'[{"filePath": "x.ts", "messages": 1}]\'\n', "not a list of findings"),
    ],
)
def test_unusable_eslint_runs_fail(
    node: Node, snapshot: Path, tmp_path: Path, body: str, message: str
) -> None:
    (snapshot / "app").mkdir()
    eslint = script(tmp_path / "eslint.sh", body)
    target = Target(APP, snapshot, FILES[:1])
    with pytest.raises(ToolFailedError, match=message):
        eslint_lint(target, node=node, script=eslint, work=tmp_path / "work")
