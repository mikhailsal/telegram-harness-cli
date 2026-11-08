from dataclasses import replace
from pathlib import Path

import pytest

from quality.catalog import build_registry
from quality.engine import SuiteReport, SuiteSettings, run_suites
from quality.engine.suites import UNDECLARED, UNKNOWN
from quality.gitio import staged
from quality.policy import Profile, Role
from quality.testing import Suite
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, policy_toml
from quality.testkit.suites import ScriptedRunner

RULES = {rule.id: rule for rule in build_registry()}
APP = ComponentSpec(id="a", root="a")
TOOL = ComponentSpec(id="b", root="b", role="tooling", integration=("integration",))
BOTH = (Suite.UNIT, Suite.INTEGRATION)


@pytest.fixture
def project(tmp_path: Path) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml(APP, TOOL))
    repo.write("README.md", "# Project\n")
    for name in ("a", "b"):
        repo.write(f"{name}/src/{name}.py", "def f():\n    return 1\n")
        repo.write(f"{name}/tests/test_{name}.py", "def test_f():\n    assert True\n")
    repo.write("b/integration/test_flow.py", "def test_flow():\n    assert True\n")
    repo.commit_all("base")
    return repo


def run(repo: GitRepo, runner: ScriptedRunner, **options: object) -> SuiteReport:
    git = repo.repository()
    settings = replace(SuiteSettings({Profile.PYTHON: runner}, RULES), **options)
    return run_suites(git, staged(git), settings)


def problems(report: SuiteReport) -> list[tuple[str, str]]:
    return [(diagnostic.rule, diagnostic.message) for diagnostic in report.diagnostics]


def ran(runner: ScriptedRunner) -> list[tuple[str, Suite]]:
    return [(request.component.id, request.suite) for request in runner.requests]


def test_named_components_replace_the_affected_selection(project: GitRepo) -> None:
    runner = ScriptedRunner({}, cover=True)
    assert run(project, runner).tested == ()
    report = run(project, runner, components=frozenset({"b"}))
    assert (report.diagnostics, report.tested) == ((), ("b",))
    assert ran(runner) == [("b", Suite.UNIT)]


def test_unknown_names_are_usage_errors_and_nothing_runs(project: GitRepo) -> None:
    runner = ScriptedRunner({}, cover=True)
    report = run(project, runner, components=frozenset({"a", "zeta", "ghost"}))
    assert problems(report) == [
        (UNKNOWN.id, "'ghost' is not an active component"),
        (UNKNOWN.id, "'zeta' is not an active component"),
    ]
    assert (report.tested, runner.prepared) == ((), [])


def test_a_role_selects_every_active_component_with_it(project: GitRepo) -> None:
    runner = ScriptedRunner({}, cover=True)
    report = run(project, runner, role=Role.TOOLING, components=frozenset({"a"}))
    assert (report.diagnostics, report.tested) == ((), ("b",))


def test_a_role_without_components_is_a_usage_error(project: GitRepo) -> None:
    runner = ScriptedRunner({}, cover=True)
    report = run(project, runner, role=Role.HELPER, required=True)
    assert problems(report) == [(UNKNOWN.id, "no active component has the helper role")]
    assert runner.prepared == []


def test_required_suites_must_be_declared_by_a_selected_component(project: GitRepo) -> None:
    runner = ScriptedRunner({}, cover=True)
    report = run(project, runner, everything=True, suites=BOTH, required=True)
    assert (report.diagnostics, report.tested) == ((), ("a", "b"))
    assert ran(runner) == [("a", Suite.UNIT), ("b", Suite.UNIT), ("b", Suite.INTEGRATION)]
    selected = run(project, runner, components=frozenset({"a"}), suites=BOTH, required=True)
    assert problems(selected) == [(UNDECLARED.id, "no integration suite is declared by a")]
    every = (Suite.UNIT, Suite.ACCEPTANCE)
    report = run(project, runner, everything=True, suites=every, required=True)
    assert problems(report) == [(UNDECLARED.id, "no acceptance suite is declared by a, b")]


def test_optional_suites_are_skipped_where_undeclared(project: GitRepo) -> None:
    runner = ScriptedRunner({}, cover=True)
    report = run(project, runner, components=frozenset({"a"}), suites=BOTH)
    assert (report.diagnostics, report.tested) == ((), ("a",))
    assert ran(runner) == [("a", Suite.UNIT)]


def test_an_empty_selection_requires_nothing(project: GitRepo) -> None:
    runner = ScriptedRunner({}, cover=True)
    report = run(project, runner, suites=(Suite.ACCEPTANCE,), required=True)
    assert (report.diagnostics, report.tested, runner.prepared) == ((), (), [])


def test_other_levels_prepare_only_the_components_declaring_them(project: GitRepo) -> None:
    runner = ScriptedRunner({}, cover=True)
    suites = (Suite.INTEGRATION,)
    report = run(project, runner, everything=True, suites=suites, required=True)
    assert (report.diagnostics, report.tested, runner.prepared) == ((), ("b",), ["b"])
    assert ran(runner) == [("b", Suite.INTEGRATION)]
