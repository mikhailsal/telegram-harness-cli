"""The `greet` reference fixture: a Python library with a command line (R08).

Consumer code imports the package in the unit suite; the e2e suite builds a
zipapp from the sources and runs it as a user would. Its manifest proves the
library and CLI requirements; acceptance tests break it in each way a
scenario can fail.
"""

from __future__ import annotations

from types import MappingProxyType

from quality.testkit.policies import ComponentSpec

SPEC = ComponentSpec(
    id="greet",
    root="greet",
    e2e=("e2e",),
    capabilities=("library", "cli"),
    scenarios="scenarios.toml",
)

LIBRARY = '''\
"""Greetings for code that imports the package and for the command line."""


def greeting(name: str) -> str:
    """Return the greeting for `name`; a blank name is refused."""
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("a name is required")
    return f"Hello, {cleaned}!"
'''

CLI = '''\
"""The `greet NAME` command."""

import sys

from greet import greeting


def main(argv: list[str]) -> int:
    """Print the greeting for the one argument; exit 2 on a usage error."""
    if len(argv) != 1:
        print("usage: greet NAME", file=sys.stderr)
        return 2
    try:
        print(greeting(argv[0]))
    except ValueError as error:
        print(f"greet: {error}", file=sys.stderr)
        return 2
    return 0


def run() -> None:
    """Run the command with the process arguments."""
    raise SystemExit(main(sys.argv[1:]))
'''

UNIT_TESTS = """\
import sys

import pytest

from greet import greeting
from greet.cli import main, run


def test_consumers_get_a_greeting() -> None:
    assert greeting("  Ada ") == "Hello, Ada!"


def test_a_blank_name_is_refused() -> None:
    with pytest.raises(ValueError, match="a name is required"):
        greeting(" ")


def test_the_command_prints_the_greeting(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["Ada"]) == 0
    assert capsys.readouterr().out == "Hello, Ada!\\n"


@pytest.mark.parametrize("argv", [[], ["a", "b"], [" "]])
def test_usage_errors_exit_2(argv: list[str]) -> None:
    assert main(argv) == 2


def test_run_exits_with_the_status_of_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["greet", "Ada"])
    with pytest.raises(SystemExit) as raised:
        run()
    assert raised.value.code == 0
"""

E2E_TESTS = """\
import subprocess
import sys
import zipapp
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"


def sources(path: Path) -> bool:
    return "__pycache__" not in path.parts


@pytest.fixture(scope="module")
def artifact(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("dist") / "greet.pyz"
    zipapp.create_archive(SOURCE, target, main="greet.cli:run", filter=sources)
    return target


def launch(artifact: Path, *args: str) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(artifact), *args]
    return subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)


def test_the_built_archive_greets(artifact: Path) -> None:
    result = launch(artifact, "World")
    assert (result.returncode, result.stdout) == (0, "Hello, World!\\n")


def test_a_blank_name_is_refused_with_status_2(artifact: Path) -> None:
    result = launch(artifact, " ")
    assert (result.returncode, result.stderr) == (2, "greet: a name is required\\n")
"""

MANIFEST = """\
schema_version = 1
component = "greet"

[[scenarios]]
id = "consumer-greeting"
summary = "Code that imports the package gets a greeting and a refusal of a blank name."
capability = "library"
level = "unit"
stage = "commit"
requirements = ["library.public-api"]
tests = [
    "tests.test_greet::test_consumers_get_a_greeting",
    "tests.test_greet::test_a_blank_name_is_refused",
]

[[scenarios]]
id = "archive-smoke"
summary = "The built zipapp starts and greets."
capability = "cli"
level = "e2e"
stage = "commit"
requirements = ["cli.smoke"]
tests = ["e2e.test_zipapp::test_the_built_archive_greets"]

[[scenarios]]
id = "archive-refusal"
summary = "The built zipapp refuses a blank name with exit status 2."
capability = "cli"
level = "e2e"
stage = "commit"
requirements = ["cli.flows"]
tests = ["e2e.test_zipapp::test_a_blank_name_is_refused_with_status_2"]
"""

FILES = MappingProxyType(
    {
        "src/greet/__init__.py": LIBRARY,
        "src/greet/cli.py": CLI,
        "tests/test_greet.py": UNIT_TESTS,
        "e2e/test_zipapp.py": E2E_TESTS,
        "scenarios.toml": MANIFEST,
    }
)
