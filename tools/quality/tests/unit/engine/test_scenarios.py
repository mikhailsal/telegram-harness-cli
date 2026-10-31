import os
from datetime import date
from pathlib import Path

import pytest

from quality.catalog import build_registry
from quality.cli.main import command_table
from quality.diagnostics import Severity
from quality.engine import Report, Settings, SuiteReport, SuiteSettings, check_candidate, run_suites
from quality.engine.scenarios import manifest_path, required_at, stage_suites
from quality.gitio import staged
from quality.policy import Profile, Suite
from quality.policy.scenarios import Stage
from quality.scenarios.reconcile import ScenarioSummary
from quality.scenarios.validate import Subject
from quality.testing import Outcome, SuiteRequest, SuiteRun
from quality.testkit.candidates import pinned_tools
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, build_policy, policy_toml, quarantine_toml
from quality.testkit.scenariorepo import FLOWS, SMOKE, SPEC, scenario_repo, scenario_runner
from quality.testkit.scenarios import build_manifest, manifest_toml
from quality.testkit.suites import ScriptedRunner

TODAY = date(2026, 10, 20)
RULES = {rule.id: rule for rule in build_registry()}
PASSED = Outcome.PASSED


@pytest.fixture
def project(tmp_path: Path) -> GitRepo:
    return scenario_repo(tmp_path / "repo")


def check(repo: GitRepo, tests: ScriptedRunner, stage: Stage = Stage.COMMIT) -> Report:
    git = repo.repository()
    settings = Settings(
        tools=pinned_tools(),
        commands=command_table(),
        rules=RULES,
        today=TODAY,
        profiles={Profile.PYTHON: tests},
        stage=stage,
    )
    return check_candidate(git, staged(git), settings)


def run_tests(repo: GitRepo, tests: ScriptedRunner, *suites: Suite) -> SuiteReport:
    git = repo.repository()
    settings = SuiteSettings({Profile.PYTHON: tests}, RULES, suites=suites, today=TODAY)
    return run_suites(git, staged(git), settings)


def found(report: Report | SuiteReport) -> list[tuple[str, str]]:
    return [(d.rule, d.message) for d in report.diagnostics]


def suites_run(tests: ScriptedRunner) -> list[Suite]:
    return [request.suite for request in tests.requests]


def test_the_commit_stage_runs_and_proves_the_commit_scenarios(project: GitRepo) -> None:
    tests = scenario_runner()
    report = check(project, tests)
    assert found(report) == []
    assert suites_run(tests) == [Suite.UNIT, Suite.E2E]
    assert report.scenarios == (ScenarioSummary("app", 1, 1, deferred=1),)


def test_the_merge_stage_also_runs_the_merge_scenarios(project: GitRepo) -> None:
    tests = scenario_runner()
    report = check(project, tests, Stage.MERGE)
    assert found(report) == []
    assert suites_run(tests) == [Suite.UNIT, Suite.INTEGRATION, Suite.E2E]
    assert report.scenarios == (ScenarioSummary("app", 2, 2),)


def test_a_failed_scenario_test_fails_the_check(project: GitRepo) -> None:
    report = check(project, scenario_runner(e2e=(("smoke", Outcome.FAILED),)))
    assert [rule for rule, _ in found(report)] == ["tests.failed", "scenarios.failed"]
    assert report.scenarios == (ScenarioSummary("app", 1, 0, deferred=1),)


def test_a_renamed_test_leaves_its_scenario_unproved(project: GitRepo) -> None:
    report = check(project, scenario_runner(e2e=(("smoke_renamed", PASSED),)))
    assert found(report) == [
        (
            "scenarios.absent",
            (
                "scenario 'smoke' (cli, e2e): the e2e suite has no case 'tests::smoke'; "
                "similar: 'tests::smoke_renamed'"
            ),
        )
    ]


def test_stale_results_leave_the_scenarios_of_their_suite_unverified(project: GitRepo) -> None:
    def stale(request: SuiteRequest, done: SuiteRun) -> SuiteRun:
        if request.suite is Suite.E2E:
            os.utime(done.results, (1_000_000, 1_000_000))
        return done

    tests = scenario_runner()
    tests.hook = stale
    report = check(project, tests)
    assert [rule for rule, _ in found(report)] == ["tests.evidence", "scenarios.unverified"]
    assert report.scenarios == (ScenarioSummary("app", 1, 0, deferred=1),)


def quarantine(project: GitRepo, *tables: str) -> None:
    project.write("quality.toml", policy_toml(SPEC, extra="\n".join(tables)))
    project.git("add", "-A")


def test_a_quarantined_failure_suspends_the_scenarios_its_entry_names(project: GitRepo) -> None:
    quarantine(project, quarantine_toml("tests::flow", "flows", expires="2026-10-20"))
    cases = (("flow", Outcome.FAILED), ("other", PASSED))
    report = check(project, scenario_runner(integration=cases), Stage.MERGE)
    assert [(d.rule, d.severity) for d in report.diagnostics] == [
        ("tests.quarantined", Severity.WARNING),
        ("scenarios.suspended", Severity.WARNING),
    ]
    assert report.scenarios == (ScenarioSummary("app", 2, 1, suspended=1),)


def test_an_expired_quarantine_no_longer_applies(project: GitRepo) -> None:
    quarantine(project, quarantine_toml("tests::flow", "flows", expires="2026-10-19"))
    report = check(project, scenario_runner(integration=(("flow", Outcome.FAILED),)), Stage.MERGE)
    assert [rule for rule, _ in found(report)] == [
        "quarantine.expired",
        "tests.failed",
        "scenarios.failed",
    ]


def test_the_suites_command_proves_the_scenarios_of_the_suites_it_ran(project: GitRepo) -> None:
    tests = scenario_runner()
    report = run_tests(project, tests, Suite.INTEGRATION)
    assert found(report) == []
    assert suites_run(tests) == [Suite.INTEGRATION]
    assert report.scenarios == (ScenarioSummary("app", 1, 1, deferred=1),)
    report = run_tests(project, scenario_runner(), Suite.UNIT)
    assert report.scenarios == (ScenarioSummary("app", 0, 0, deferred=2),)


def test_the_suites_command_reports_manifest_problems_of_tested_components(
    project: GitRepo,
) -> None:
    project.write("scenarios.toml", manifest_toml(SMOKE))
    quarantine(project, quarantine_toml("tests::x", expires="2026-10-01"))
    report = run_tests(project, scenario_runner(), Suite.E2E)
    assert [rule for rule, _ in found(report)] == ["quarantine.expired", "scenarios.uncovered"]
    other = ComponentSpec(id="lib", root="lib", capabilities=("cli",), scenarios="scenarios.toml")
    project.write("quality.toml", policy_toml(SPEC, other))
    project.write("lib/src/lib/__init__.py", "")
    project.write("lib/tests/test_lib.py", "def test_lib():\n    assert True\n")
    project.write("lib/scenarios.toml", manifest_toml(component="lib"))
    project.git("add", "-A")
    git = project.repository()
    settings = SuiteSettings(
        {Profile.PYTHON: scenario_runner()},
        RULES,
        suites=(Suite.E2E,),
        components=frozenset({"app"}),
    )
    report = run_suites(git, staged(git), settings)
    assert {d.component for d in report.diagnostics} == {"app"}


def test_a_manifest_path_is_relative_to_its_component() -> None:
    root, nested, bare = build_policy(
        SPEC,
        ComponentSpec(id="lib", root="lib", scenarios="docs/scenarios.toml"),
        ComponentSpec(id="other", root="other"),
    ).components
    assert manifest_path(root) == "scenarios.toml"
    assert manifest_path(nested) == "lib/docs/scenarios.toml"
    assert manifest_path(bare) is None


def test_each_stage_runs_the_unit_suite_and_the_levels_of_its_scenarios() -> None:
    component = build_policy(SPEC).components[0]
    subject = Subject(component, build_manifest(SMOKE, FLOWS), "scenarios.toml")
    assert stage_suites(subject, Stage.COMMIT) == (Suite.UNIT, Suite.E2E)
    assert stage_suites(subject, Stage.MERGE) == (Suite.UNIT, Suite.INTEGRATION, Suite.E2E)
    assert stage_suites(None, Stage.MERGE) == (Suite.UNIT,)
    commit, merge = build_manifest(SMOKE, FLOWS).scenarios
    assert (required_at(Stage.COMMIT)(commit), required_at(Stage.COMMIT)(merge)) == (True, False)
    assert (required_at(Stage.MERGE)(commit), required_at(Stage.MERGE)(merge)) == (True, True)
