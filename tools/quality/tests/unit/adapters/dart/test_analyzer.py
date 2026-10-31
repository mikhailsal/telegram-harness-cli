import json
from pathlib import Path

import pytest

from quality.adapters.dart.analyzer import LINT, TYPES, dart_analyze
from quality.adapters.dart.config import CONFIG
from quality.adapters.native import ToolFailedError
from quality.adapters.target import Target
from quality.boundedio import JsonValue
from quality.diagnostics import Location
from quality.testkit.dartfakes import FakeDart, dart_component

APP = dart_component()
FILES = ("app/lib/a.dart", "app/test/a_test.dart")


@pytest.fixture
def target(snapshot: Path) -> Target:
    (snapshot / "app" / "test").mkdir(parents=True)
    for name in ("pubspec.yaml", "analysis_options.yaml", "test/analysis_options.yaml"):
        (snapshot / "app" / name).write_text("")
    return Target(APP, snapshot, FILES)


def diagnostic(
    file: str | None, kind: str = "COMPILE_TIME_ERROR", start: JsonValue = None, **fields: JsonValue
) -> dict[str, JsonValue]:
    where: dict[str, JsonValue] = {} if file is None else {"file": file}
    if start is not None:
        where["range"] = {"start": start, "end": start}
    found: dict[str, JsonValue] = {
        "code": "unused_import",
        "severity": "WARNING",
        "type": kind,
        "location": where,
        "problemMessage": "Unused import.",
    }
    found.update(fields)
    return found


def answer(fake_dart: FakeDart, output: JsonValue, status: int = 0) -> None:
    (fake_dart.root / "analysis.json").write_text(json.dumps(output))
    fake_dart.handle("analyze", f"cat '{fake_dart.root}/analysis.json'\nexit {status}\n")


def analyzed(target: Target, fake_dart: FakeDart, tmp_path: Path) -> list[tuple[str, str, str]]:
    found = dart_analyze(target, sdk=fake_dart.sdk, home=tmp_path / "home")
    assert {item.component for item in found} <= {"app"}
    return [(item.rule, where(item.location), item.message) for item in found]


def where(location: Location | None) -> str:
    assert location is not None
    parts = (location.path, location.line, location.column)
    return ":".join(str(part) for part in parts if part is not None)


def test_a_clean_analysis_reports_nothing(
    target: Target, fake_dart: FakeDart, tmp_path: Path
) -> None:
    answer(fake_dart, {"version": 1, "diagnostics": []})
    assert analyzed(target, fake_dart, tmp_path) == []
    ((cwd, *argv),) = fake_dart.calls()
    assert cwd == str(target.directory)
    assert argv[1:4] == ["analyze", "--fatal-infos", "--format=json"]
    settings = ("pubspec.yaml", "analysis_options.yaml", "test/analysis_options.yaml")
    assert argv[4:] == [
        *target.arguments(),
        *(str(target.directory / name) for name in settings),
    ]


def test_lints_and_type_errors_are_located_in_the_inventory(
    target: Target, fake_dart: FakeDart, tmp_path: Path
) -> None:
    lib, test = target.arguments()
    start: JsonValue = {"offset": 9, "line": 2, "column": 5}
    answer(
        fake_dart,
        {
            "version": 1,
            "diagnostics": [
                diagnostic(lib, "LINT", start, code="avoid_print", problemMessage="No print."),
                diagnostic(test, "STATIC_WARNING", {"line": "2", "column": 1}),
                diagnostic(test, "HINT", start={"line": 3}),
                diagnostic(lib, location={"file": lib, "range": "whole"}),
            ],
        },
        status=3,
    )
    assert analyzed(target, fake_dart, tmp_path) == [
        (LINT.id, "app/lib/a.dart:2:5", "[avoid_print] No print."),
        (TYPES.id, "app/test/a_test.dart", "[unused_import] Unused import."),
        (TYPES.id, "app/test/a_test.dart:3", "[unused_import] Unused import."),
        (TYPES.id, "app/lib/a.dart", "[unused_import] Unused import."),
    ]


def test_the_pubspec_and_the_analysis_options_are_checked_as_configuration(
    target: Target, fake_dart: FakeDart, tmp_path: Path
) -> None:
    options, pubspec = target.directory / "analysis_options.yaml", target.directory / "pubspec.yaml"
    start: JsonValue = {"line": 11, "column": 7}
    deprecated = {"code": "deprecated_lint", "problemMessage": "Deprecated."}
    answer(
        fake_dart,
        {
            "diagnostics": [
                diagnostic(str(options), "STATIC_WARNING", start, **deprecated),
                diagnostic(str(pubspec), "LINT", code="sort_pub_dependencies"),
            ]
        },
        status=2,
    )
    assert analyzed(target, fake_dart, tmp_path) == [
        (CONFIG.id, "app/analysis_options.yaml:11:7", "[deprecated_lint] Deprecated."),
        (LINT.id, "app/pubspec.yaml", "[sort_pub_dependencies] Unused import."),
    ]


def test_a_diagnostic_outside_the_inventory_is_located_at_the_pubspec(
    target: Target, fake_dart: FakeDart, snapshot: Path, tmp_path: Path
) -> None:
    other = str(snapshot / "app" / "lib" / "other.dart")
    answer(
        fake_dart,
        {
            "diagnostics": [
                diagnostic(other, start={"line": 4, "column": 1}),
                diagnostic("/elsewhere/x.dart", "LINT"),
                diagnostic(None, location="none"),
            ]
        },
        status=2,
    )
    message = "[unused_import] Unused import."
    assert analyzed(target, fake_dart, tmp_path) == [
        (TYPES.id, "app/pubspec.yaml", f"app/lib/other.dart:4: {message}"),
        (LINT.id, "app/pubspec.yaml", f"/elsewhere/x.dart: {message}"),
        (TYPES.id, "app/pubspec.yaml", f"the analysis: {message}"),
    ]


def test_a_component_at_the_repository_root_is_located_at_its_pubspec(
    snapshot: Path, fake_dart: FakeDart, tmp_path: Path
) -> None:
    target = Target(dart_component(root="."), snapshot, ("lib/a.dart",))
    answer(fake_dart, {"diagnostics": [diagnostic(None)]}, status=2)
    ((_, where, _),) = analyzed(target, fake_dart, tmp_path)
    assert where == "pubspec.yaml"


@pytest.mark.parametrize(
    ("output", "status", "message"),
    [
        ([], 0, "output is not a JSON object"),
        ({"diagnostics": {}}, 0, "output is not a list of findings"),
        ({"diagnostics": [diagnostic(None, code=None)]}, 3, "a diagnostic has no code or message"),
        ({"diagnostics": [diagnostic(None, problemMessage=1)]}, 3, "has no code or message"),
        ({"diagnostics": []}, 64, "exit status 64"),
    ],
)
def test_unusable_analyzer_output_is_a_tool_failure(
    target: Target, fake_dart: FakeDart, output: JsonValue, status: int, message: str
) -> None:
    answer(fake_dart, output, status)
    with pytest.raises(ToolFailedError, match=message):
        dart_analyze(target, sdk=fake_dart.sdk, home=target.snapshot.parent / "home")


def test_output_that_is_not_json_is_a_tool_failure(
    target: Target, fake_dart: FakeDart, tmp_path: Path
) -> None:
    fake_dart.handle("analyze", "echo 'Analyzing...'\n")
    with pytest.raises(ToolFailedError, match="dart analyze output"):
        dart_analyze(target, sdk=fake_dart.sdk, home=tmp_path / "home")
