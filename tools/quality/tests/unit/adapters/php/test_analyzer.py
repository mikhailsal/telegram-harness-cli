import json
from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.native import ToolFailedError
from quality.adapters.php.analyzer import BINARY, MEMORY, TYPES, phpstan
from quality.adapters.php.runtime import Php
from quality.adapters.target import Target
from quality.boundedio import JsonValue
from quality.testkit.phpfakes import FakePhp, php_component
from quality.testkit.suites import direct

APP = php_component()
FILES = ("app/src/A.php", "app/tests/ATest.php")


@pytest.fixture
def target(snapshot: Path) -> Target:
    (snapshot / "app").mkdir()
    return Target(APP, snapshot, FILES)


def answer(fake_php: FakePhp, output: JsonValue, status: int = 0) -> None:
    (fake_php.root / "report.json").write_text(json.dumps(output))
    fake_php.handle("phpstan", f"cat '{fake_php.root}/report.json'\nexit {status}\n")


def report(files: JsonValue, errors: JsonValue = None) -> JsonValue:
    return {"totals": {}, "files": files, "errors": errors or []}


def analysed(target: Target, php: Php, tmp_path: Path) -> list[tuple[str, str, str | None]]:
    found = phpstan(target, php=php, settings=tmp_path / "phpstan.neon", home=tmp_path / "home")
    assert {item.rule for item in found} <= {TYPES.id}
    assert {item.component for item in found} <= {"app"}
    places = [item.location for item in found]
    return [
        (
            f"{place.path}:{place.line}" if place else "",
            item.message,
            (item.data or {}).get("symbol"),
        )
        for place, item in zip(places, found, strict=True)
    ]


def test_a_clean_analysis_reports_nothing(
    target: Target, fake_php: FakePhp, php: Php, tmp_path: Path
) -> None:
    answer(fake_php, report({}))
    assert analysed(target, php, tmp_path) == []
    ((cwd, *argv),) = fake_php.calls()
    assert cwd == str(target.directory)
    assert argv[2:] == [
        str(target.directory / BINARY),
        "analyse",
        f"--configuration={tmp_path / 'phpstan.neon'}",
        "--level=max",
        "--error-format=json",
        "--no-progress",
        "--no-interaction",
        MEMORY,
        "--",
        *target.arguments(),
    ]


def test_each_error_names_its_identifier_as_the_symbol(
    target: Target, fake_php: FakePhp, php: Php, tmp_path: Path
) -> None:
    source, test = target.arguments()
    messages: JsonValue = [
        {"message": "Method A::f() has no return type.", "line": 4, "identifier": "missingType"},
        {"message": "Syntax error", "line": None},
    ]
    files: JsonValue = {
        source: {"errors": 2, "messages": messages},
        "tests/ATest.php": {"errors": 0, "messages": []},
    }
    answer(fake_php, report(files), status=1)
    assert analysed(target, php, tmp_path) == [
        ("app/src/A.php:4", "[missingType] Method A::f() has no return type.", "missingType"),
        ("app/src/A.php:None", "Syntax error", None),
    ]
    del test


def test_the_analysis_runs_behind_the_network_isolation(
    target: Target, fake_php: FakePhp, php: Php, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[tuple[str, ...]] = []

    def contained(argv: tuple[str, ...]) -> tuple[tuple[str, ...], str | None]:
        started.append(argv)
        return direct()(argv)

    monkeypatch.setattr(native, "contained", contained)
    answer(fake_php, report({}))
    analysed(target, php, tmp_path)
    assert [argv[0] for argv in started] == [str(php.executable)]


@pytest.mark.parametrize(
    ("output", "status", "message"),
    [
        ([], 0, "output is not a JSON object"),
        ({"files": []}, 0, "the report has no 'files' object"),
        (report({}, ["Invalid configuration"]), 1, "errors outside the files"),
        (report({"src/A.php": []}), 1, "the entry of src/A.php is not an object"),
        (report({"src/A.php": {"messages": {}}}), 1, "output is not a list of findings"),
        (report({"src/A.php": {"messages": [{"line": 3}]}}), 1, "an error has no message"),
        (report({}), 1, "exit status 1 without errors"),
        (report({}), 255, "exit status 255"),
    ],
    ids=["list", "files", "general", "entry", "messages", "message", "silent", "status"],
)
def test_an_unusable_report_is_a_tool_failure(
    target: Target, fake_php: FakePhp, output: JsonValue, status: int, message: str
) -> None:
    php, tmp_path = fake_php.php, target.snapshot.parent
    answer(fake_php, output, status)
    with pytest.raises(ToolFailedError, match=message):
        analysed(target, php, tmp_path)
