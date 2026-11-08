import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.python.environment import Environments, Uv
from quality.adapters.python.profile import RULES, PythonProfile
from quality.cli.local import core_tool, local_uv
from quality.diagnostics import Diagnostic
from quality.policy import Component
from quality.testing import StaticRequest, Suite, SuiteRequest
from quality.testkit.policies import ComponentSpec, build_policy
from quality.testkit.pythonproject import python_project

CORE = "def sign(value: int) -> int:\n    if value < 0:\n        return -1\n    return 1\n"
TESTS = "from app.core import sign\n\n\ndef test_sign() -> None:\n    assert sign(-2) == -1\n"
FILES = {
    "src/app/__init__.py": "",
    "src/app/core.py": CORE,
    "src/app/messy.py": "import os\nx=1\n",
    "src/app/typed.py": "def name(value: int) -> str:\n    return value\n",
    "src/app/shapes.pyi": "def area(side: int) -> int: ...\n",
    "tests/test_core.py": TESTS,
}
CHECKED = tuple(f"app/{path}" for path in FILES)


@dataclass(frozen=True, slots=True)
class Prepared:
    profile: PythonProfile
    component: Component
    snapshot: Path
    work: Path


def component() -> Component:
    (found,) = build_policy(ComponentSpec(root="app")).components
    return found


def unprepared(tmp_path: Path) -> PythonProfile:
    """Return a profile whose uv cannot run, so any sync would fail loudly."""
    uv = Uv(tmp_path / "missing-uv", tmp_path)
    return PythonProfile(ruff=tmp_path, pyright=tmp_path, environments=Environments(uv))


def located(found: list[Diagnostic]) -> list[tuple[str, str, int | None]]:
    return sorted(
        (d.rule, d.location.path, d.location.line) for d in found if d.location is not None
    )


@pytest.fixture(scope="module")
def prepared(tmp_path_factory: pytest.TempPathFactory) -> Prepared:
    base = tmp_path_factory.mktemp("profile")
    snapshot, work = base / "snapshot", base / "work"
    python_project(snapshot / "app", FILES)
    environments = Environments(local_uv())
    profile = PythonProfile(core_tool("ruff"), core_tool("pyright"), environments)
    app = component()
    assert profile.prepare(app, snapshot, work) == []
    return Prepared(profile, app, snapshot, work)


def test_the_profile_reports_configuration_format_lint_and_type_rules(tmp_path: Path) -> None:
    rules = unprepared(tmp_path).rules
    assert rules == RULES
    assert [rule.id for rule in rules] == [
        "python.config",
        "python.format",
        "python.lint",
        "python.types",
    ]


def test_a_component_without_a_project_is_not_synced(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    found = unprepared(tmp_path).prepare(component(), tmp_path, tmp_path / "work")
    assert [(d.rule, d.location and d.location.path) for d in found] == [
        ("python.config", "app/pyproject.toml")
    ]


def test_a_configuration_problem_stops_before_the_sync(tmp_path: Path) -> None:
    python_project(tmp_path / "app", {})
    (tmp_path / "app" / "uv.lock").unlink()
    found = unprepared(tmp_path).prepare(component(), tmp_path, tmp_path / "work")
    assert [d.location and d.location.path for d in found] == ["app/uv.lock"]


def test_a_stale_lockfile_stops_before_the_sync(tmp_path: Path) -> None:
    project = python_project(tmp_path / "app", {})
    pyproject = project / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace("dependencies = []", 'dependencies = ["idna"]')
    )
    profile = PythonProfile(tmp_path, tmp_path, Environments(local_uv()))
    found = profile.prepare(component(), tmp_path, tmp_path / "work")
    assert [(d.rule, d.location and d.location.path) for d in found] == [
        ("python.config", "app/uv.lock")
    ]
    assert found[0].message.startswith("uv.lock does not match pyproject.toml: ")
    assert not (tmp_path / "work").exists()


def test_gates_need_a_prepared_component(tmp_path: Path) -> None:
    profile, app = unprepared(tmp_path), component()
    with pytest.raises(native.ToolFailedError, match="'app' was not prepared"):
        profile.static(StaticRequest(app, tmp_path, tmp_path / "work", ("app/src/a.py",)))
    output = tmp_path / "out"
    request = SuiteRequest(app, Suite.UNIT, ("tests",), tmp_path, output, 0.0, tmp_path / "work")
    with pytest.raises(native.ToolFailedError, match="'app' was not prepared"):
        profile.run(request)


def test_only_python_modules_are_expected_in_coverage(tmp_path: Path) -> None:
    profile = unprepared(tmp_path)
    assert profile.expected("src/app/core.py")
    assert not profile.expected("src/app/shapes.pyi")
    assert not profile.expected("src/app/data.json")


def test_static_gates_run_the_pinned_tools(prepared: Prepared) -> None:
    profile, app = prepared.profile, prepared.component
    files = ("app/README.md", *CHECKED)
    found = profile.static(StaticRequest(app, prepared.snapshot, prepared.work, files))
    assert located(found) == [
        ("python.format", "app/src/app/messy.py", 2),
        ("python.lint", "app/src/app/messy.py", 1),
        ("python.lint", "app/src/app/messy.py", 1),
        ("python.types", "app/src/app/messy.py", 1),
        ("python.types", "app/src/app/typed.py", 2),
    ]
    lint = sorted(d.message for d in found if d.rule == "python.lint")
    assert lint == [
        "F401: `os` imported but unused",
        "I001: Import block is un-sorted or un-formatted",
    ]
    readme = StaticRequest(app, prepared.snapshot, prepared.work, ("app/README.md",))
    assert profile.static(readme) == []
    assert profile.measure(readme) is None


def test_a_failing_tool_is_reported_and_the_others_still_run(
    prepared: Prepared, tmp_path: Path
) -> None:
    ruff = tmp_path / "ruff"
    ruff.write_text("#!/bin/sh\necho not json\n")
    ruff.chmod(0o755)
    environments = prepared.profile.environments
    profile = PythonProfile(ruff, core_tool("pyright"), environments)
    app, snapshot, work = prepared.component, prepared.snapshot, prepared.work
    assert profile.prepare(app, snapshot, work) == []
    found = profile.static(StaticRequest(app, snapshot, work, ("app/src/app/core.py",)))
    assert [d.rule for d in found] == ["tools.failed", "tools.failed"]


def test_unit_suites_run_with_coverage_of_the_sources(prepared: Prepared) -> None:
    app, snapshot, work = prepared.component, prepared.snapshot, prepared.work
    output = work / "app-unit"
    output.mkdir()
    request = SuiteRequest(app, Suite.UNIT, ("tests",), snapshot, output, time.time(), work)
    done = prepared.profile.run(request)
    assert done.returncode == 0
    assert done.coverage is not None
    report = prepared.profile.coverage(done.coverage.read_bytes(), request)
    assert report.branches_measured
    core = report.files["app/src/app/core.py"]
    assert (len(core.covered), len(core.executable)) == (3, 4)
