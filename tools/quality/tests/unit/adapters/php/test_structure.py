import json
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.php.config import Prefix
from quality.adapters.php.helper import MAIN, Helper, digest
from quality.adapters.php.structure import Structures, autoload, measurements, php_file
from quality.adapters.target import Target
from quality.boundedio import JsonValue
from quality.metrics import FileMetrics, FunctionMetrics, ImportRef, StructureError
from quality.testkit.phpfakes import FakePhp, php_component, write_helper

APP = php_component()
FILES = ("app/src/A.php", "app/src/B.php")
PREFIXES = (Prefix("App\\", ("src/",)),)


@pytest.fixture
def target(snapshot: Path) -> Target:
    (snapshot / "app" / "src").mkdir(parents=True)
    return Target(APP, snapshot, FILES)


def function(name: str = "f", line: int = 3) -> dict[str, JsonValue]:
    return {
        "name": name,
        "line": line,
        "code_lines": 2,
        "complexity": 1,
        "nesting": 0,
        "parameters": 1,
    }


def entry(
    target: Target, path: str, *, imports: JsonValue = None, **fields: JsonValue
) -> dict[str, JsonValue]:
    found: dict[str, JsonValue] = {
        "file": str(target.snapshot / path),
        "functions": [function()],
        "imports": imports if imports is not None else [],
        "loadable": True,
        "units": 1,
        "error": None,
    }
    found.update(fields)
    return found


def test_an_entry_has_metrics_and_edges_within_the_snapshot(target: Target) -> None:
    snapshot = target.snapshot
    imports: JsonValue = [
        {"specifier": "App\\B", "line": 4, "resolved": str(snapshot / "app/src/B.php")},
        {"specifier": "./B.php", "line": 9, "resolved": str(snapshot / "app/src/B.php")},
        {"specifier": "App\\A", "line": 5, "resolved": str(snapshot / "app/src/A.php")},
        {"specifier": "Psr\\Log", "line": 6, "resolved": None},
        {"specifier": "/etc/php.php", "line": 7, "resolved": "/etc/php.php"},
    ]
    found = php_file(target, entry(target, "app/src/A.php", imports=imports, units=2))
    assert (found.path, found.loadable, found.units) == ("app/src/A.php", True, 2)
    assert found.edges == {"app/src/B.php": 4}
    assert found.metrics == FileMetrics(
        "app/src/A.php",
        (FunctionMetrics("f", 3, 2, 1, 0, 1),),
        (
            ImportRef("App\\B", 4),
            ImportRef("./B.php", 9),
            ImportRef("App\\A", 5),
            ImportRef("Psr\\Log", 6),
            ImportRef("/etc/php.php", 7),
        ),
    )


def test_a_file_that_does_not_parse_is_a_structure_error(target: Target) -> None:
    error: JsonValue = {"line": 3, "message": "Syntax error, unexpected '{'"}
    found = php_file(target, entry(target, "app/src/A.php", error=error, loadable=False, units=0))
    assert isinstance(found.metrics, StructureError)
    assert (found.metrics.path, found.metrics.line) == ("app/src/A.php", 3)
    assert (found.loadable, found.units, found.edges) == (False, 0, {})
    unlocated: JsonValue = {"line": None, "message": "cannot read the file"}
    found = php_file(target, entry(target, "app/src/A.php", error=unlocated))
    assert isinstance(found.metrics, StructureError)
    assert found.metrics.line is None


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"loadable": "yes"}, "'loadable' is not a boolean"),
        ({"units": None}, "'units' is not"),
        ({"error": "broken"}, "'error' is not an object"),
        ({"functions": [{"name": "f"}]}, "'line' is not"),
        ({"imports": [{"specifier": "A", "line": 1, "resolved": 3}]}, "'resolved' is not"),
    ],
    ids=["loadable", "units", "error", "function", "resolved"],
)
def test_a_malformed_entry_is_a_tool_failure(
    target: Target, fields: dict[str, JsonValue], message: str
) -> None:
    with pytest.raises(ToolFailedError, match=message):
        php_file(target, entry(target, "app/src/A.php", **fields))


def test_measurements_separate_metrics_errors_and_dependencies(target: Target) -> None:
    error: JsonValue = {"line": 1, "message": "Syntax error"}
    imports: JsonValue = [
        {"specifier": "App\\B", "line": 4, "resolved": str(target.snapshot / "app/src/B.php")}
    ]
    first = php_file(target, entry(target, "app/src/A.php", imports=imports))
    second = php_file(target, entry(target, "app/src/B.php", error=error))
    found = measurements([first, second])
    assert found.files == (first.metrics,)
    assert [problem.path for problem in found.errors] == ["app/src/B.php"]
    assert found.dependencies == {"app/src/A.php": {"app/src/B.php": 4}}


def test_the_helper_reads_absolute_autoload_directories(tmp_path: Path) -> None:
    prefixes = (Prefix("App\\", ("src/", "lib/")), Prefix("", ("legacy/",)))
    assert autoload(tmp_path, prefixes) == [
        {"prefix": "App\\", "directories": [str(tmp_path / "src/"), str(tmp_path / "lib/")]},
        {"prefix": "", "directories": [str(tmp_path / "legacy/")]},
    ]


@pytest.fixture
def helper(tmp_path: Path, fake_php: FakePhp) -> Helper:
    source = write_helper(tmp_path / "helper")
    cache = tmp_path / "cache"
    installed = cache / digest(source, fake_php.php.version) / MAIN
    installed.parent.mkdir(parents=True)
    installed.write_text("<?php\n")
    return Helper(source, cache)


def test_each_file_is_asked_about_once_per_work_area(
    target: Target, fake_php: FakePhp, helper: Helper, tmp_path: Path
) -> None:
    answers = [entry(target, path) for path in FILES]
    (fake_php.root / "answer.json").write_text(json.dumps({"protocol": 1, "files": answers}))
    fake_php.handle("main.php", f"cat '{fake_php.root}/answer.json'\n")
    structures = Structures(helper)
    work = tmp_path / "work"
    found = structures.files(target, php=fake_php.php, work=work, prefixes=PREFIXES)
    assert [file.path for file in found] == list(FILES)
    single = Target(APP, target.snapshot, FILES[1:])
    assert structures.files(single, php=fake_php.php, work=work, prefixes=PREFIXES) == found[1:]
    assert fake_php.scripts() == ["main.php"]
    structures.files(single, php=fake_php.php, work=tmp_path / "other", prefixes=PREFIXES)
    assert fake_php.scripts() == ["main.php", "main.php"]
    (request,) = (json.loads(path.read_text()) for path in (work / "php-requests").iterdir())
    assert request == {
        "protocol": 1,
        "autoload": autoload(target.directory, PREFIXES),
        "files": list(target.arguments()),
    }
    assert fake_php.calls()[0][0] == str(target.directory)


def test_an_answer_without_a_file_is_a_tool_failure(
    target: Target, fake_php: FakePhp, helper: Helper, tmp_path: Path
) -> None:
    answer = {"protocol": 1, "files": [entry(target, FILES[0])]}
    (fake_php.root / "answer.json").write_text(json.dumps(answer))
    fake_php.handle("main.php", f"cat '{fake_php.root}/answer.json'\n")
    with pytest.raises(ToolFailedError, match=r"the structure answer lacks app/src/B\.php"):
        Structures(helper).files(target, php=fake_php.php, work=tmp_path, prefixes=PREFIXES)
