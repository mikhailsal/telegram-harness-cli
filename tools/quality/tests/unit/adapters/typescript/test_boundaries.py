import json
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.target import Target
from quality.adapters.typescript.boundaries import import_boundaries, package_name
from quality.adapters.typescript.environment import Node
from quality.boundedio import JsonValue
from quality.testkit.nodefakes import browser_component, script

SERVER = '[components.browser]\nserver_modules = ["src/server/**"]\nserver_packages = ["pg"]'
WEB = browser_component(extra=SERVER)


def answer(tmp_path: Path, files: JsonValue) -> Path:
    (tmp_path / "answer.json").write_text(json.dumps({"protocol": 1, "files": files}))
    body = f"cp \"$2\" '{tmp_path / 'request.json'}'\ncat '{tmp_path / 'answer.json'}'\n"
    return script(tmp_path / "helper.sh", body)


def imported(
    specifier: str, line: int, resolved: str | None = None, *, builtin: bool = False
) -> dict[str, JsonValue]:
    return {
        "specifier": specifier,
        "line": line,
        "resolved": resolved,
        "builtin": builtin,
        "typeOnly": False,
    }


@pytest.mark.parametrize(
    ("specifier", "package"),
    [
        ("pg", "pg"),
        ("pg/lib/client", "pg"),
        ("@scope/db", "@scope/db"),
        ("@scope/db/x", "@scope/db"),
    ],
)
def test_a_specifier_names_its_package(specifier: str, package: str) -> None:
    assert package_name(specifier) == package


def test_builtins_server_modules_and_server_packages_are_reported(
    node: Node, snapshot: Path, tmp_path: Path
) -> None:
    directory = snapshot / "web"
    (directory / "src").mkdir(parents=True)
    (directory / "tsconfig.json").write_text("{}")
    server = str(directory / "src/server/db.ts")
    imports: list[JsonValue] = [
        imported("node:fs", 1, builtin=True),
        imported("@/server/db", 2, server),
        imported("pg/lib/client", 3),
        imported("./view", 4, str(directory / "src/view.tsx")),
        imported("react", 5),
        imported("../../outside", 6, "/elsewhere/outside.ts"),
        {**imported("node:path", 7, builtin=True), "typeOnly": True},
        {**imported("./server/db", 8, server), "typeOnly": True},
    ]
    files: JsonValue = [{"file": str(directory / "src/App.tsx"), "imports": imports}]
    helper = answer(tmp_path, files)
    target = Target(WEB, snapshot, ("web/src/App.tsx",))
    found = import_boundaries(target, node=node, work=tmp_path / "work", program=helper)
    assert {(d.rule, d.location.path if d.location else None) for d in found} == {
        ("typescript.boundaries", "web/src/App.tsx")
    }
    assert [(d.location.line if d.location else None, d.message) for d in found] == [
        (1, "browser code imports the Node builtin 'node:fs'"),
        (2, "browser code imports the server-only module web/src/server/db.ts"),
        (3, "browser code imports the server-only package 'pg'"),
    ]
    request = json.loads((tmp_path / "request.json").read_text())
    assert request["files"] == [str(directory / "src/App.tsx")]
    assert request["tsconfig"] == str(directory / "tsconfig.json")


def test_a_malformed_response_fails_the_tool(node: Node, snapshot: Path, tmp_path: Path) -> None:
    (snapshot / "web").mkdir()
    files: JsonValue = [{"file": str(snapshot / "web/a.ts"), "imports": [{"line": 1}]}]
    helper = answer(tmp_path, files)
    target = Target(WEB, snapshot, ("web/a.ts",))
    with pytest.raises(ToolFailedError):
        import_boundaries(target, node=node, work=tmp_path / "work", program=helper)
