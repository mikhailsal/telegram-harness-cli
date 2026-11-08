from pathlib import Path

import pytest

from quality.adapters.dart.formatter import FORMAT, UNPARSABLE, dart_format
from quality.adapters.native import ToolFailedError
from quality.adapters.target import Target
from quality.testkit.dartfakes import FakeDart, dart_component

APP = dart_component()
FILES = ("app/lib/a.dart", "app/test/a_test.dart")


@pytest.fixture
def target(snapshot: Path) -> Target:
    (snapshot / "app").mkdir()
    return Target(APP, snapshot, FILES)


def located(target: Target, fake_dart: FakeDart, tmp_path: Path) -> list[str | None]:
    found = dart_format(target, sdk=fake_dart.sdk, home=tmp_path / "home")
    assert {diagnostic.rule for diagnostic in found} <= {FORMAT.id}
    assert {diagnostic.component for diagnostic in found} <= {"app"}
    return [diagnostic.location.path if diagnostic.location else None for diagnostic in found]


def test_formatted_files_pass(target: Target, fake_dart: FakeDart, tmp_path: Path) -> None:
    assert located(target, fake_dart, tmp_path) == []
    ((cwd, *argv),) = fake_dart.calls()
    assert cwd == str(target.directory)
    assert argv == [
        "--suppress-analytics",
        "format",
        "--output=none",
        "--set-exit-if-changed",
        *target.arguments(),
    ]


def test_each_changed_file_is_reported(target: Target, fake_dart: FakeDart, tmp_path: Path) -> None:
    lib, test = target.arguments()
    fake_dart.handle(
        "format", f"echo 'Changed {lib}'\necho 'Changed {test}'\necho 'Formatted 2 files'\nexit 1\n"
    )
    assert located(target, fake_dart, tmp_path) == list(FILES)


def test_an_unparsable_file_is_left_to_the_analyzer(
    target: Target, fake_dart: FakeDart, tmp_path: Path
) -> None:
    lib, _ = target.arguments()
    fake_dart.handle("format", f"echo '{UNPARSABLE}:' >&2\necho 'Changed {lib}'\nexit 65\n")
    assert located(target, fake_dart, tmp_path) == ["app/lib/a.dart"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("echo 'usage error' >&2\nexit 65\n", "exit status 65: usage error"),
        ("echo 'crashed' >&2\nexit 255\n", "exit status 255: crashed"),
    ],
)
def test_other_formatter_failures_are_tool_failures(
    target: Target, fake_dart: FakeDart, tmp_path: Path, body: str, message: str
) -> None:
    fake_dart.handle("format", body)
    with pytest.raises(ToolFailedError, match=message):
        dart_format(target, sdk=fake_dart.sdk, home=tmp_path / "home")
