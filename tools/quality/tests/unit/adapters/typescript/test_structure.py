import json
from pathlib import Path

from quality.adapters.target import Target
from quality.adapters.typescript.environment import Node
from quality.adapters.typescript.structure import measure_files
from quality.boundedio import JsonValue
from quality.metrics import FileMetrics, FunctionMetrics, ImportRef, Measurements
from quality.testkit.nodefakes import script, typescript_component

APP = typescript_component()
FUNCTION: dict[str, JsonValue] = {
    "name": "sign",
    "line": 1,
    "code_lines": 5,
    "complexity": 3,
    "nesting": 1,
    "parameters": 1,
}


def answer(tmp_path: Path, files: JsonValue) -> Path:
    (tmp_path / "answer.json").write_text(json.dumps({"protocol": 1, "files": files}))
    body = f"cp \"$2\" '{tmp_path / 'request.json'}'\ncat '{tmp_path / 'answer.json'}'\n"
    return script(tmp_path / "helper.sh", body)


def request(tmp_path: Path) -> dict[str, JsonValue]:
    return json.loads((tmp_path / "request.json").read_text())


def test_functions_imports_and_errors_are_measured(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    directory = snapshot / "app"
    directory.mkdir()
    (directory / "tsconfig.json").write_text("{}")
    files: JsonValue = [
        {
            "file": str(directory / "src/a.ts"),
            "functions": [FUNCTION],
            "imports": [
                {"specifier": "./b.ts", "line": 1, "resolved": str(directory / "src/b.ts")},
                {"specifier": "@lib/b.ts", "line": 2, "resolved": str(directory / "src/b.ts")},
                {"specifier": "vitest", "line": 3, "resolved": None},
                {"specifier": "../../x", "line": 4, "resolved": "/elsewhere/x.ts"},
            ],
            "error": None,
        },
        {
            "file": str(directory / "src/b.ts"),
            "functions": [],
            "imports": [],
            "error": {"line": 7, "message": "'}' expected."},
        },
        {
            "file": str(directory / "src/c.ts"),
            "functions": [],
            "imports": [],
            "error": {"line": None, "message": "analysis failed"},
        },
    ]
    helper = answer(tmp_path, files)
    target = Target(APP, snapshot, ("app/src/a.ts", "app/src/b.ts", "app/src/c.ts"))
    measured = measure_files(target, node=node, work=tmp_path / "work", program=helper)
    imports = (
        ImportRef("./b.ts", 1),
        ImportRef("@lib/b.ts", 2),
        ImportRef("vitest", 3),
        ImportRef("../../x", 4),
    )
    function = FunctionMetrics("sign", 1, code_lines=5, complexity=3, nesting=1, parameters=1)
    metrics = FileMetrics("app/src/a.ts", (function,), imports)
    assert measured.files == (metrics,)
    assert [(e.path, e.line, e.message) for e in measured.errors] == [
        ("app/src/b.ts", 7, "'}' expected."),
        ("app/src/c.ts", None, "analysis failed"),
    ]
    assert measured.dependencies == {"app/src/a.ts": {"app/src/b.ts": 1}}
    assert request(tmp_path)["tsconfig"] == str(directory / "tsconfig.json")


def test_a_component_without_tsconfig_is_measured_with_defaults(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    (snapshot / "app").mkdir()
    helper = answer(tmp_path, [])
    target = Target(APP, snapshot, ())
    measured = measure_files(target, node=node, work=tmp_path / "work", program=helper)
    assert measured == Measurements(())
    assert request(tmp_path) == {
        "protocol": 1,
        "typescript": str(snapshot / "app"),
        "tsconfig": None,
        "files": [],
    }
