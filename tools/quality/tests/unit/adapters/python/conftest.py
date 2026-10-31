import sys
from collections.abc import Callable
from pathlib import Path

import pytest

PYPROJECT = """\
[project]
name = "app"
version = "0.1.0"
requires-python = ">=3.14"

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F"]

[tool.pyright]
typeCheckingMode = "strict"

[tool.pytest.ini_options]
pythonpath = ["src"]
"""


def locked(name: str, source: str) -> str:
    return f'[[package]]\nname = "{name}"\nversion = "1.0"\nsource = {{ {source} }}\n'


REGISTRY = 'registry = "https://pypi.org/simple"'
LOCK = "\n".join(
    [
        'version = 1\nrequires-python = ">=3.14"\n',
        locked("app", 'editable = "."'),
        locked("coverage", REGISTRY),
        locked("pytest", REGISTRY),
        locked("pytest_cov", REGISTRY),
    ]
)


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "snapshot"
    root.mkdir()
    return root


@pytest.fixture
def component(snapshot: Path) -> Path:
    """Write a valid Python component at `app/` of the snapshot."""
    directory = snapshot / "app"
    (directory / "src" / "pkg").mkdir(parents=True)
    (directory / "pyproject.toml").write_text(PYPROJECT)
    (directory / "uv.lock").write_text(LOCK)
    (directory / ".python-version").write_text("3.14.8\n")
    return directory


@pytest.fixture
def tools() -> Path:
    """Return the directory of the core environment's Ruff and Pyright."""
    return Path(sys.executable).parent


@pytest.fixture
def fake(tmp_path: Path) -> Callable[[str, int], Path]:
    """Build an executable that prints `stdout`, writes a line to stderr, and exits `code`."""
    count = 0

    def build(stdout: str, code: int) -> Path:
        nonlocal count
        count += 1
        output = tmp_path / f"fake-{count}.out"
        output.write_text(stdout)
        script = tmp_path / f"fake-{count}"
        script.write_text(f"#!/bin/sh\ncat '{output}'\necho 'fake stderr' >&2\nexit {code}\n")
        script.chmod(0o755)
        return script

    return build
