from datetime import date
from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.python.ruff import FORMAT
from quality.catalog import build_registry
from quality.cli.main import command_table
from quality.diagnostics import Diagnostic, Severity
from quality.engine import Report, Settings, check_candidate
from quality.engine.check import UNAVAILABLE
from quality.engine.common import MEASURED_RULES
from quality.engine.ran import ran_rules
from quality.gitio import staged
from quality.metrics import FileMetrics, FunctionMetrics, Measurements, StructureError
from quality.policy import Profile
from quality.testing import StaticRequest
from quality.testkit.candidates import pinned_tools
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, build_policy, policy_toml
from quality.testkit.suites import ScriptedRunner

TODAY = date(2026, 10, 20)
README = "# Project\n\nSee [agent instructions](AGENTS.md).\n"
AGENTS = "# Agents\n\nRun `quality check` before committing.\n"
PY = ComponentSpec(id="py", root="py")
ARCHITECTURE = """
[components.architecture]
layers = ["api", "core"]
modules = [
    { name = "api", paths = ["src/api.ts"] },
    { name = "core", paths = ["src/core.ts"] },
]
"""
EXCEPTION = """
[[exceptions]]
rule = "structure.complexity"
path = "{path}"
symbol = "f"
reason = "pending"
owner = "team"
expires = 2026-12-31
"""
MEASURED = {rule.id for rule in MEASURED_RULES}
HELPER = "structure helper"


def web(extra: str = "") -> ComponentSpec:
    return ComponentSpec(id="web", root="web", profile="typescript", extra=extra)


def settings(typescript: ScriptedRunner, *, everything: bool = False) -> Settings:
    return Settings(
        tools=pinned_tools(),
        commands=command_table(),
        rules={rule.id: rule for rule in build_registry()},
        today=TODAY,
        profiles={
            Profile.PYTHON: ScriptedRunner({}, cover=True),
            Profile.TYPESCRIPT: typescript,
        },
        everything=everything,
    )


@pytest.fixture
def project(tmp_path: Path) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml(PY, web()))
    repo.write("README.md", README)
    repo.write("AGENTS.md", AGENTS)
    repo.write("py/src/py/__init__.py", "")
    repo.write("py/tests/test_py.py", "def test_ok():\n    assert True\n")
    repo.write("web/src/api.ts", "export const api = 1;\n")
    repo.write("web/src/core.ts", "export const core = 1;\n")
    repo.write("web/tests/core.test.ts", "export {};\n")
    repo.git("add", "-A")
    return repo


def check(repo: GitRepo, typescript: ScriptedRunner, *, everything: bool = False) -> Report:
    git = repo.repository()
    return check_candidate(git, staged(git), settings(typescript, everything=everything))


def findings(report: Report) -> list[Diagnostic]:
    """Leave out the information that the scripted coverage measures no TypeScript lines."""
    return [d for d in report.diagnostics if d.severity is not Severity.INFO]


def rules_of(report: Report) -> list[str]:
    return [diagnostic.rule for diagnostic in findings(report)]


def metrics(path: str, complexity: int = 1) -> FileMetrics:
    return FileMetrics(path, (FunctionMetrics("f", 1, 3, complexity, 0, 1),), ())


def test_rules_that_ran() -> None:
    python = build_policy().components
    typescript = build_policy(ComponentSpec(profile="typescript")).components
    assert ran_rules(python, (), {}).rules >= MEASURED
    assert not MEASURED & ran_rules(typescript, (), {}).rules
    assert ran_rules(typescript, typescript, {}).rules >= MEASURED


def test_adapter_rules_ran_only_over_checked_components() -> None:
    adapter = ScriptedRunner({}, rules=(UNAVAILABLE,))
    two = build_policy(ComponentSpec(id="a", root="a"), ComponentSpec(id="b", root="b"))
    profiles = {Profile.PYTHON: adapter}
    first, _ = two.components
    every = ran_rules(two.components, two.components, profiles)
    assert every.covers(UNAVAILABLE.id, "a/x.py")
    assert every.covers(UNAVAILABLE.id, "b/x.py")
    partly = ran_rules(two.components, (first,), profiles)
    assert partly.covers(UNAVAILABLE.id, "a/x.py")
    assert not partly.covers(UNAVAILABLE.id, "b/x.py")
    assert not ran_rules((), (), profiles).covers(UNAVAILABLE.id, "a/x.py")
    assert partly.covers("structure.complexity", "b/x.py")


def test_measurements_are_checked_against_the_thresholds(project: GitRepo) -> None:
    error = StructureError("web/src/broken.ts", 2, "';' expected")
    measured = Measurements((metrics("web/src/api.ts", 11),), (error,))
    report = check(project, ScriptedRunner({}, cover=True, measurements={"web": measured}))
    assert rules_of(report) == ["structure.unparseable", "structure.complexity"]
    assert {diagnostic.component for diagnostic in findings(report)} == {"web"}


def test_measured_dependencies_between_sources_are_checked(project: GitRepo) -> None:
    project.write("quality.toml", policy_toml(PY, web(ARCHITECTURE)))
    project.git("add", "-A")
    files = tuple(metrics(path) for path in ("web/src/api.ts", "web/src/core.ts"))
    dependencies = {
        "web/src/core.ts": {"web/src/api.ts": 1, "web/src/core.ts": 2},
        "web/src/api.ts": {"web/tests/core.test.ts": 1},
        "web/tests/core.test.ts": {"web/src/core.ts": 1},
    }
    measured = Measurements(files, (), dependencies)
    report = check(project, ScriptedRunner({}, cover=True, measurements={"web": measured}))
    assert rules_of(report) == ["architecture.forbidden-dependency"]
    (upward,) = findings(report)
    assert upward.location is not None
    assert (upward.location.path, upward.location.line) == ("web/src/core.ts", 1)


def test_an_unchecked_component_keeps_its_structure_exceptions(project: GitRepo) -> None:
    exception = EXCEPTION.format(path="web/src/api.ts")
    project.write("quality.toml", policy_toml(PY, web(), extra=exception))
    project.git("add", "-A")
    project.git("commit", "-q", "-m", "first")
    project.write("py/src/py/__init__.py", "X = 1\n")
    project.git("add", "-A")
    typescript = ScriptedRunner({}, cover=True, measurements={"web": Measurements(())})
    report = check(project, typescript)
    assert (report.diagnostics, report.tested) == ((), ("py",))
    report = check(project, typescript, everything=True)
    assert rules_of(report) == ["exceptions.unused"]
    complex_api = Measurements((metrics("web/src/api.ts", 11),))
    excepted = ScriptedRunner({}, cover=True, measurements={"web": complex_api})
    report = check(project, excepted, everything=True)
    assert (rules_of(report), report.excepted) == ([], 1)


def test_python_structure_exceptions_are_judged_without_the_adapter(project: GitRepo) -> None:
    exception = EXCEPTION.format(path="py/src/py/__init__.py")
    project.write("quality.toml", policy_toml(PY, web(), extra=exception))
    project.git("add", "-A")
    project.git("commit", "-q", "-m", "first")
    project.write("web/src/api.ts", "export const api = 2;\n")
    project.git("add", "-A")
    report = check(project, ScriptedRunner({}, cover=True))
    assert report.tested == ("web",)
    assert rules_of(report) == ["exceptions.unused"]


class Unmeasurable(ScriptedRunner):
    def measure(self, request: StaticRequest) -> Measurements | None:
        raise native.failure(HELPER, "crashed")


def test_an_adapter_whose_parser_fails_still_reports_its_gates(project: GitRepo) -> None:
    finding = FORMAT.diagnostic("would reformat", component="web")
    adapter = Unmeasurable({}, cover=True, findings={"web": [finding]})
    report = check(project, adapter)
    assert rules_of(report) == ["python.format", "tools.failed"]
    assert findings(report)[1].message == "structure helper: crashed"
    (request,) = adapter.statics
    assert request.files == ("web/src/api.ts", "web/src/core.ts", "web/tests/core.test.ts")
    assert request.directory == request.snapshot / "web"


def test_the_static_request_names_configuration_scripts_and_resources(project: GitRepo) -> None:
    project.write("web/eslint.config.js", "export default [];\n")
    project.write("web/package.json", "{}\n")
    project.write("web/src/app.css", "a {}\n")
    project.write("web/dist/app.js.map", "{}\n")
    project.git("add", "-A")
    adapter = ScriptedRunner({}, cover=True)
    check(project, adapter)
    (request,) = adapter.statics
    assert request.configs == ("web/eslint.config.js",)
    assert request.resources == ("web/package.json", "web/src/app.css")
