from collections.abc import Callable
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.python.fixer import PythonFixer
from quality.diagnostics import Diagnostic, Location
from quality.testkit.policies import ComponentSpec, build_policy

(APP,) = build_policy(ComponentSpec(root="app")).components
MESSY = "import os\nx=1\n"


def rendered(found: Diagnostic) -> str:
    return found.location.render() if found.location else ""


def test_safe_fixes_and_formatting_are_applied(
    component: Path, snapshot: Path, tools: Path
) -> None:
    (component / "src/pkg/a.py").write_text(MESSY)
    (component / "src/pkg/b.pyi").write_text("def f(x:int)->int: ...\n")
    files = ("app/src/pkg/a.py", "app/src/pkg/b.pyi")
    assert PythonFixer(tools / "ruff").fix(APP, snapshot, files) == []
    assert (component / "src/pkg/a.py").read_text() == "x = 1\n"
    assert (component / "src/pkg/b.pyi").read_text() == "def f(x: int) -> int: ...\n"


def test_findings_without_a_safe_fix_remain(component: Path, snapshot: Path, tools: Path) -> None:
    (component / "src/pkg/a.py").write_text("import os\nprint(y)\n")
    (component / "src/pkg/b.py").write_text("def f(:\n")
    files = ("app/src/pkg/a.py", "app/src/pkg/b.py")
    found = PythonFixer(tools / "ruff").fix(APP, snapshot, files)
    assert {d.rule for d in found} == {"python.lint"}
    assert sorted({(rendered(d), d.message.split(":")[0]) for d in found}) == [
        ("app/src/pkg/a.py:1:7", "F821"),
        ("app/src/pkg/b.py:1:7", "invalid-syntax"),
        ("app/src/pkg/b.py:2:1", "invalid-syntax"),
    ]
    assert (component / "src/pkg/a.py").read_text() == "print(y)\n"
    assert (component / "src/pkg/b.py").read_text() == "def f(:\n"


def test_excluded_files_are_still_fixed(component: Path, snapshot: Path, tools: Path) -> None:
    text = (component / "pyproject.toml").read_text()
    excluded = text.replace("[tool.ruff]\n", '[tool.ruff]\nextend-exclude = ["src"]\n')
    (component / "pyproject.toml").write_text(excluded)
    (component / "src/pkg/a.py").write_text(MESSY)
    assert PythonFixer(tools / "ruff").fix(APP, snapshot, ("app/src/pkg/a.py",)) == []
    assert (component / "src/pkg/a.py").read_text() == "x = 1\n"


def test_unusable_settings_stop_before_ruff_runs(
    component: Path, snapshot: Path, tmp_path: Path
) -> None:
    (component / "pyproject.toml").write_text('[project]\nname = "app"\n')
    (component / "src/pkg/a.py").write_text(MESSY)
    found = PythonFixer(tmp_path / "missing").fix(APP, snapshot, ("app/src/pkg/a.py",))
    assert [(d.rule, d.location, d.message) for d in found] == [
        (
            "python.config",
            Location("app/pyproject.toml"),
            "select Ruff lint rules explicitly with lint.select",
        )
    ]
    assert (component / "src/pkg/a.py").read_text() == MESSY


def test_only_python_files_are_fixed(component: Path, snapshot: Path, tmp_path: Path) -> None:
    (component / "notes.txt").write_text(MESSY)
    files = ("app/notes.txt", "app/pyproject.toml")
    assert PythonFixer(tmp_path / "missing").fix(APP, snapshot, files) == []
    assert (component / "notes.txt").read_text() == MESSY


@pytest.mark.parametrize("code", [3, 127])
def test_a_ruff_failure_is_a_tool_failure(
    component: Path, snapshot: Path, fake: Callable[[str, int], Path], code: int
) -> None:
    (component / "src/pkg/a.py").write_text(MESSY)
    with pytest.raises(ToolFailedError) as raised:
        PythonFixer(fake("", code)).fix(APP, snapshot, ("app/src/pkg/a.py",))
    assert raised.value.diagnostic.rule == "tools.failed"
    assert raised.value.diagnostic.message.startswith("ruff: ")
    assert (component / "src/pkg/a.py").read_text() == MESSY
