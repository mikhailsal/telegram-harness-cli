import json
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.php.formatter import BINARY, FORMAT, cs_check
from quality.adapters.php.runtime import Php
from quality.adapters.target import Target
from quality.boundedio import JsonValue
from quality.testkit.phpfakes import FakePhp, php_component

APP = php_component()
FILES = ("app/src/A.php", "app/tests/ATest.php")


@pytest.fixture
def target(snapshot: Path) -> Target:
    (snapshot / "app").mkdir()
    return Target(APP, snapshot, FILES)


def answer(fake_php: FakePhp, output: JsonValue, status: int = 0) -> None:
    (fake_php.root / "report.json").write_text(json.dumps(output))
    fake_php.handle("php-cs-fixer", f"cat '{fake_php.root}/report.json'\nexit {status}\n")


def located(target: Target, php: Php, tmp_path: Path) -> list[str | None]:
    found = cs_check(target, php=php, settings=tmp_path / "cs.php", home=tmp_path / "home")
    assert {diagnostic.rule for diagnostic in found} <= {FORMAT.id}
    assert {diagnostic.component for diagnostic in found} <= {"app"}
    return [diagnostic.location.path if diagnostic.location else None for diagnostic in found]


def test_formatted_files_pass(target: Target, fake_php: FakePhp, php: Php, tmp_path: Path) -> None:
    answer(fake_php, {"files": []})
    assert located(target, php, tmp_path) == []
    ((cwd, *argv),) = fake_php.calls()
    assert cwd == str(target.directory)
    assert argv[2:] == [
        str(target.directory / BINARY),
        "check",
        f"--config={tmp_path / 'cs.php'}",
        "--using-cache=no",
        "--format=json",
        "--show-progress=none",
        "--no-interaction",
        "--sequential",
        "--",
        *target.arguments(),
    ]


def test_each_file_to_rewrite_is_reported(
    target: Target, fake_php: FakePhp, php: Php, tmp_path: Path
) -> None:
    files: JsonValue = [{"name": "src/A.php"}, {"name": str(target.directory / "tests/ATest.php")}]
    answer(fake_php, {"files": files}, status=8)
    assert located(target, php, tmp_path) == list(FILES)
    found = cs_check(target, php=php, settings=tmp_path / "cs.php", home=tmp_path / "home")
    assert found[0].message == "the file does not follow PER Coding Style; run `./quality fix`"


def test_an_unparsable_file_is_left_to_the_analyzer(
    target: Target, fake_php: FakePhp, php: Php, tmp_path: Path
) -> None:
    answer(fake_php, {"files": [{"name": "src/A.php"}]}, status=12)
    assert located(target, php, tmp_path) == ["app/src/A.php"]
    answer(fake_php, {"files": []}, status=4)
    assert located(target, php, tmp_path) == []


@pytest.mark.parametrize(
    ("output", "status", "message"),
    [
        ([], 0, "output is not a JSON object"),
        ({"files": {}}, 0, "output is not a list of findings"),
        ({"files": [{"name": "/elsewhere/A.php"}]}, 8, "outside the snapshot"),
        ({"files": []}, 16, "exit status 16"),
    ],
    ids=["list", "files", "outside", "status"],
)
def test_an_unusable_report_is_a_tool_failure(
    target: Target, fake_php: FakePhp, output: JsonValue, status: int, message: str
) -> None:
    php, tmp_path = fake_php.php, target.snapshot.parent
    answer(fake_php, output, status)
    with pytest.raises(ToolFailedError, match=message):
        located(target, php, tmp_path)
