import json
from pathlib import Path

import pytest

from quality.adapters.dart.helper import Helper
from quality.adapters.dart.structure import DartFile, Structures, measurements
from quality.adapters.native import ToolFailedError
from quality.adapters.target import Target
from quality.boundedio import JsonValue
from quality.metrics import FileMetrics, FunctionMetrics, ImportRef, StructureError
from quality.testkit.dartfakes import FakeDart, dart_component, write_helper

APP = dart_component()
FUNCTION: dict[str, JsonValue] = {
    "name": "add",
    "line": 3,
    "code_lines": 4,
    "complexity": 2,
    "nesting": 1,
    "parameters": 2,
}


def entry(path: Path, **fields: JsonValue) -> dict[str, JsonValue]:
    found: dict[str, JsonValue] = {
        "file": str(path),
        "part": False,
        "functions": [],
        "imports": [],
        "error": None,
    }
    found.update(fields)
    return found


@pytest.fixture
def structures(tmp_path: Path, snapshot: Path) -> Structures:
    (snapshot / "app" / "lib").mkdir(parents=True)
    return Structures(Helper(write_helper(tmp_path / "helper"), tmp_path / "helper-cache"))


def answer(fake_dart: FakeDart, files: JsonValue) -> None:
    (fake_dart.root / "answer.json").write_text(json.dumps({"protocol": 1, "files": files}))
    log = fake_dart.root / "requests.jsonl"
    body = f"cat \"$3\" >> '{log}'\necho >> '{log}'\ncat '{fake_dart.root}/answer.json'\n"
    fake_dart.handle("dill", body)


def requests(fake_dart: FakeDart) -> list[dict[str, JsonValue]]:
    lines = (fake_dart.root / "requests.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def problem(metrics: FileMetrics | StructureError) -> tuple[str, int | None, str]:
    assert isinstance(metrics, StructureError)
    return metrics.path, metrics.line, metrics.message


def test_functions_imports_parts_and_errors_are_read(
    structures: Structures, fake_dart: FakeDart, snapshot: Path, tmp_path: Path
) -> None:
    lib = snapshot / "app" / "lib"
    answer(
        fake_dart,
        [
            entry(
                lib / "a.dart",
                functions=[FUNCTION],
                imports=[
                    {
                        "specifier": "package:calc/b.dart",
                        "line": 1,
                        "resolved": str(lib / "b.dart"),
                    },
                    {"specifier": "b.dart", "line": 2, "resolved": str(lib / "b.dart")},
                    {"specifier": "dart:math", "line": 3, "resolved": None},
                    {"specifier": "package:path/p.dart", "line": 4, "resolved": "/pub/p.dart"},
                ],
            ),
            entry(lib / "b.dart", part=True, error={"line": 7, "message": "Expected ';'."}),
            entry(lib / "c.dart", error={"line": None, "message": "unreadable"}),
        ],
    )
    target = Target(APP, snapshot, ("app/lib/a.dart", "app/lib/b.dart", "app/lib/c.dart"))
    a, b, c = structures.files(target, sdk=fake_dart.sdk, work=tmp_path / "work", package="calc")
    function = FunctionMetrics("add", 3, code_lines=4, complexity=2, nesting=1, parameters=2)
    references = (
        ImportRef("package:calc/b.dart", 1),
        ImportRef("b.dart", 2),
        ImportRef("dart:math", 3),
        ImportRef("package:path/p.dart", 4),
    )
    metrics = FileMetrics("app/lib/a.dart", (function,), references)
    assert a == DartFile("app/lib/a.dart", False, metrics, {"app/lib/b.dart": 1})
    assert (b.path, b.part, b.edges) == ("app/lib/b.dart", True, {})
    assert [problem(file.metrics) for file in (b, c)] == [
        ("app/lib/b.dart", 7, "Expected ';'."),
        ("app/lib/c.dart", None, "unreadable"),
    ]
    assert requests(fake_dart) == [
        {
            "protocol": 1,
            "package": {"name": "calc", "root": str(snapshot / "app")},
            "files": [str(snapshot / path) for path in target.files],
        }
    ]
    measured = measurements([a, b, c])
    assert measured.files == (metrics,)
    assert measured.errors == (b.metrics, c.metrics)
    assert measured.dependencies == {"app/lib/a.dart": {"app/lib/b.dart": 1}}


def test_each_file_is_asked_about_once_per_work_area(
    structures: Structures, fake_dart: FakeDart, snapshot: Path, tmp_path: Path
) -> None:
    lib = snapshot / "app" / "lib"
    answer(fake_dart, [entry(lib / "a.dart"), entry(lib / "b.dart")])
    work = tmp_path / "work"
    both = Target(APP, snapshot, ("app/lib/a.dart", "app/lib/b.dart"))
    structures.files(both, sdk=fake_dart.sdk, work=work, package="calc")
    first = Target(APP, snapshot, ("app/lib/b.dart",))
    (b,) = structures.files(first, sdk=fake_dart.sdk, work=work, package="calc")
    assert b.path == "app/lib/b.dart"
    answer(fake_dart, [entry(lib / "c.dart"), entry(lib / "b.dart")])
    more = Target(APP, snapshot, ("app/lib/a.dart", "app/lib/c.dart"))
    found = structures.files(more, sdk=fake_dart.sdk, work=work, package="calc")
    assert [file.path for file in found] == ["app/lib/a.dart", "app/lib/c.dart"]
    structures.files(first, sdk=fake_dart.sdk, work=tmp_path / "other", package="calc")
    assert [request["files"] for request in requests(fake_dart)] == [
        [str(lib / "a.dart"), str(lib / "b.dart")],
        [str(lib / "c.dart")],
        [str(lib / "b.dart")],
    ]


def test_an_answer_that_misses_a_file_is_a_tool_failure(
    structures: Structures, fake_dart: FakeDart, snapshot: Path, tmp_path: Path
) -> None:
    answer(fake_dart, [entry(snapshot / "app" / "lib" / "a.dart")])
    target = Target(APP, snapshot, ("app/lib/a.dart", "app/lib/b.dart", "app/lib/c.dart"))
    with pytest.raises(ToolFailedError, match=r"lacks app/lib/b\.dart, app/lib/c\.dart$"):
        structures.files(target, sdk=fake_dart.sdk, work=tmp_path / "work", package="calc")


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"part": "no"}, "'part' is not a boolean"),
        ({"error": "broken"}, "'error' is not an object"),
        ({"imports": [{"specifier": "a.dart", "line": 1, "resolved": 3}]}, "'resolved'"),
        ({"functions": [{**FUNCTION, "nesting": -1}]}, "'nesting' is not a non-negative"),
    ],
)
def test_a_malformed_entry_is_a_tool_failure(
    structures: Structures,
    fake_dart: FakeDart,
    tmp_path: Path,
    fields: dict[str, JsonValue],
    message: str,
) -> None:
    snapshot = tmp_path / "snapshot"
    answer(fake_dart, [entry(snapshot / "app" / "lib" / "a.dart", **fields)])
    target = Target(APP, snapshot, ("app/lib/a.dart",))
    with pytest.raises(ToolFailedError, match=message):
        structures.files(target, sdk=fake_dart.sdk, work=tmp_path / "work", package="calc")
