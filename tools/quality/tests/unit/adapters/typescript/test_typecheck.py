import json
from pathlib import Path

from quality.adapters.target import Target
from quality.adapters.typescript.environment import Node
from quality.adapters.typescript.typecheck import TYPES, typescript_types
from quality.boundedio import JsonValue
from quality.diagnostics import Diagnostic
from quality.testkit.nodefakes import script, typescript_component

APP = typescript_component()


def answer(tmp_path: Path, projects: JsonValue, diagnostics: JsonValue) -> Path:
    response = {"protocol": 1, "projects": projects, "diagnostics": diagnostics}
    (tmp_path / "answer.json").write_text(json.dumps(response))
    body = f"cp \"$2\" '{tmp_path / 'request.json'}'\ncat '{tmp_path / 'answer.json'}'\n"
    return script(tmp_path / "helper.sh", body)


def located(found: list[Diagnostic]) -> list[tuple[str | None, int | None, int | None, str]]:
    assert {d.rule for d in found} <= {TYPES.id}
    return [
        (d.location.path, d.location.line, d.location.column, d.message)
        if d.location
        else (None, None, None, d.message)
        for d in found
    ]


def diagnostic(file: str | None, line: int | None, category: str = "error") -> JsonValue:
    message = "Type 'string' is not assignable to type 'number'."
    entry: dict[str, JsonValue] = {"file": file, "line": line, "column": 5}
    return {**entry, "code": 2322, "category": category, "message": message}


def test_projects_diagnostics_and_unchecked_files_are_reported(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    directory = snapshot / "app"
    directory.mkdir()
    projects: JsonValue = [
        {
            "config": str(directory / "tsconfig.json"),
            "files": [str(directory / "src/a.ts"), "/elsewhere/lib.d.ts"],
            "problems": [],
        },
        {
            "config": str(directory / "tests/tsconfig.json"),
            "files": [str(directory / "tests/b.test.ts")],
            "problems": ["strict is off"],
        },
        {"config": "/elsewhere/tsconfig.base.json", "files": [], "problems": ["noImplicitAny"]},
    ]
    diagnostics: JsonValue = [
        diagnostic(str(directory / "src/a.ts"), 3),
        diagnostic(str(directory / "src/a.ts"), 0, "warning"),
        diagnostic(str(directory / "src/a.ts"), 4, "suggestion"),
        diagnostic("/elsewhere/lib.d.ts", 1),
        diagnostic(None, None),
    ]
    helper = answer(tmp_path, projects, diagnostics)
    files = ("app/src/a.ts", "app/tests/b.test.ts", "app/src/orphan.ts")
    target = Target(APP, snapshot, files)
    found = typescript_types(target, node=node, work=tmp_path / "work", program=helper)
    error = "TS2322: Type 'string' is not assignable to type 'number'."
    assert located(found) == [
        ("app/tests/tsconfig.json", None, None, "strict is off; the profile requires strict mode"),
        ("app/tsconfig.json", None, None, "noImplicitAny; the profile requires strict mode"),
        (
            "app/src/orphan.ts",
            None,
            None,
            "no project of tsconfig.json includes the file, so it is not type-checked",
        ),
        ("app/src/a.ts", 3, 5, error),
        ("app/src/a.ts", None, None, error),
        ("app/tsconfig.json", None, None, f"/elsewhere/lib.d.ts: {error}"),
        ("app/tsconfig.json", None, None, error),
    ]
    request = json.loads((tmp_path / "request.json").read_text())
    assert request == {
        "protocol": 1,
        "typescript": str(directory),
        "tsconfig": str(directory / "tsconfig.json"),
    }


def test_a_clean_project_passes(node: Node, snapshot: Path, tmp_path: Path) -> None:
    directory = snapshot / "app"
    directory.mkdir()
    project: JsonValue = {"config": str(directory / "tsconfig.json"), "files": [], "problems": []}
    helper = answer(tmp_path, [project], [])
    target = Target(APP, snapshot, ())
    assert typescript_types(target, node=node, work=tmp_path / "work", program=helper) == []
