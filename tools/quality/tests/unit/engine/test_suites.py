import os
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from quality.adapters import native
from quality.catalog import build_registry
from quality.coverage import Counter
from quality.diagnostics import Diagnostic, Kind, Severity
from quality.engine import SuiteReport, SuiteSettings, run_suites
from quality.gitio import GitioError, staged
from quality.policy import Profile
from quality.rules.coverage import ComponentCoverage
from quality.testing import Outcome, Suite, SuiteRequest, SuiteRun
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, classification_toml, policy_toml
from quality.testkit.reports import FileSpec
from quality.testkit.suites import Hook, Script, ScriptedRunner

RULES = {rule.id: rule for rule in build_registry()}
CORE = "src/app/core.py"
FULL = {"src/app/__init__.py": FileSpec.first(0, 0), CORE: FileSpec.first(2, 2, (1, 1))}
UNIT = ("app", Suite.UNIT)
TOOL = "pytest"


@pytest.fixture
def project(tmp_path: Path) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml())
    repo.write("src/app/__init__.py", "")
    repo.write(CORE, "def add(a, b):\n    return a + b\n")
    repo.write("tests/test_core.py", "def test_add():\n    assert True\n")
    repo.git("add", "-A")
    return repo


def runner(script: Script | None = None, hook: Hook | None = None) -> ScriptedRunner:
    return ScriptedRunner({UNIT: script or Script(files=FULL)}, hook)


def run(
    repo: GitRepo, tests: ScriptedRunner, runners: Mapping[Profile, ScriptedRunner] | None = None
) -> SuiteReport:
    git = repo.repository()
    settings = SuiteSettings(runners or {Profile.PYTHON: tests}, RULES)
    return run_suites(git, staged(git), settings)


def rules_of(report: SuiteReport) -> list[str]:
    return [diagnostic.rule for diagnostic in report.diagnostics]


def only(report: SuiteReport, rule: str) -> Diagnostic:
    found = [diagnostic for diagnostic in report.diagnostics if diagnostic.rule == rule]
    assert len(found) == 1, report.diagnostics
    return found[0]


def test_a_clean_unit_run_with_full_coverage_passes(project: GitRepo) -> None:
    tests = runner()
    report = run(project, tests)
    assert report == SuiteReport(
        (), ("app",), (ComponentCoverage("app", Counter(2, 2), Counter(1, 1), Counter(2, 2)),)
    )
    [request] = tests.requests
    assert (request.suite, request.roots, request.directory) == (
        Suite.UNIT,
        ("tests",),
        request.snapshot,
    )
    assert request.output.parent != request.snapshot
    assert request.output.name.startswith("app-unit-")
    assert tests.fresh == [True]
    assert tests.parsed == [UNIT]
    assert not request.snapshot.exists()


def test_a_failed_run_is_not_rescued_by_perfect_coverage(project: GitRepo) -> None:
    cases = (("test_ok", Outcome.PASSED), ("test_bad", Outcome.FAILED))
    tests = runner(Script(cases, files=FULL))
    report = run(project, tests)
    assert rules_of(report) == ["tests.failed"]
    message = "1 of 2 tests failed; runner exit status 0; failed: tests::test_bad"
    assert report.diagnostics[0].message == f"the unit suite of 'app': {message}"
    assert (tests.parsed, report.coverage) == ([], ())


def test_a_runner_failure_with_passing_results_fails(project: GitRepo) -> None:
    report = run(project, runner(Script(returncode=1, files=FULL)))
    assert rules_of(report) == ["tests.failed"]
    assert report.diagnostics[0].message.endswith("0 of 1 tests failed; runner exit status 1")


@pytest.mark.parametrize(
    ("cases", "expected"),
    [
        ((), ["tests.none"]),
        ((("test_skip", Outcome.SKIPPED),), ["tests.none", "tests.skipped"]),
    ],
)
def test_a_unit_run_without_executed_tests_fails(
    project: GitRepo, cases: tuple[tuple[str, Outcome], ...], expected: list[str]
) -> None:
    tests = runner(Script(cases, files=FULL))
    assert rules_of(run(project, tests)) == expected
    assert tests.parsed == []


def test_a_unit_suite_with_the_network_warns_and_still_counts(project: GitRepo) -> None:
    report = run(project, runner(hook=lambda _, done: replace(done, unisolated="denied")))
    found = only(report, "tests.network")
    assert found.severity is Severity.WARNING
    assert found.message == "the unit suite of 'app' ran with the network: denied"
    assert len(report.coverage) == 1


def test_skipped_tests_warn_and_coverage_is_still_evaluated(project: GitRepo) -> None:
    cases = (("test_ok", Outcome.PASSED), ("test_skip", Outcome.SKIPPED))
    tests = runner(Script(cases, files=FULL))
    report = run(project, tests)
    assert only(report, "tests.skipped").severity is Severity.WARNING
    assert len(report.coverage) == 1


@pytest.mark.parametrize(
    ("script", "rule", "message"),
    [
        (
            Script(),
            "tests.evidence",
            "the unit suite of 'app': the runner wrote no coverage report",
        ),
        (
            Script(files=FULL, branch=False),
            "coverage.branch-unmeasured",
            "has no branch measurement",
        ),
        (
            Script(files={CORE: FULL[CORE]}),
            "coverage.unaccounted",
            "'src/app/__init__.py' of 'app'",
        ),
    ],
)
def test_incomplete_coverage_evidence_fails(
    project: GitRepo, script: Script, rule: str, message: str
) -> None:
    report = run(project, runner(script))
    assert message in only(report, rule).message


def test_files_the_profile_does_not_expect_are_not_required(project: GitRepo) -> None:
    project.write("src/app/data.json", "{}\n")
    project.write("src/app/generated.py", "# Code generated by a tool. DO NOT EDIT.\nx = 1\n")
    project.write("tests/helpers.py", "y = 2\n")
    project.write(
        "quality.toml", policy_toml(extra=classification_toml("src/app/generated.py", "generated"))
    )
    project.git("add", "-A")
    assert run(project, runner()).diagnostics == ()


def delete_results(_request: SuiteRequest, done: SuiteRun) -> SuiteRun:
    done.results.unlink()
    return done


def committed_results(request: SuiteRequest, done: SuiteRun) -> SuiteRun:
    return replace(done, results=request.snapshot / "tests" / "test_core.py")


def stale_results(_request: SuiteRequest, done: SuiteRun) -> SuiteRun:
    os.utime(done.results, (1_000_000, 1_000_000))
    return done


def stale_coverage(_request: SuiteRequest, done: SuiteRun) -> SuiteRun:
    assert done.coverage is not None
    os.utime(done.coverage, (1_000_000, 1_000_000))
    return done


def linked_coverage(_request: SuiteRequest, done: SuiteRun) -> SuiteRun:
    assert done.coverage is not None
    link = done.coverage.with_name("link.json")
    link.symlink_to(done.coverage)
    return replace(done, coverage=link)


def garbled_results(_request: SuiteRequest, done: SuiteRun) -> SuiteRun:
    done.results.write_bytes(b"<testsuites><testsuite")
    return done


@pytest.mark.parametrize(
    ("hook", "message"),
    [
        (delete_results, "report not found or not a regular file"),
        (committed_results, "report 'test_core.py' is outside the output directory of the run"),
        (stale_results, "report 'junit.xml' is older than the run"),
        (stale_coverage, "report 'coverage.json' is older than the run"),
        (linked_coverage, "report not found or not a regular file"),
        (garbled_results, "test results"),
    ],
)
def test_reports_that_are_not_fresh_evidence_of_this_run_fail(
    project: GitRepo, hook: Hook, message: str
) -> None:
    tests = runner(Script(files=FULL), hook)
    finding = only(run(project, tests), "tests.evidence")
    assert message in finding.message
    assert finding.kind is Kind.ENVIRONMENT


def test_a_runner_that_cannot_start_is_a_tool_failure(project: GitRepo) -> None:
    def broken(_request: SuiteRequest, _done: SuiteRun) -> SuiteRun:
        raise native.failure(TOOL, "interpreter not found")

    finding = only(run(project, runner(hook=broken)), "tools.failed")
    assert finding.message == "pytest: interpreter not found"


def test_a_component_without_a_runner_is_unavailable(project: GitRepo) -> None:
    report = run(project, runner(), runners={Profile.DART: runner()})
    assert only(report, "profiles.unavailable").message == (
        "'app' uses the python profile; this build has no adapter for it"
    )


def test_a_component_without_unit_roots_fails(project: GitRepo) -> None:
    project.write("quality.toml", policy_toml(ComponentSpec(unit=())))
    project.git("add", "-A")
    tests = runner()
    report = run(project, tests)
    assert only(report, "tests.none").message == "'app' declares no unit test roots"
    assert tests.requests == []


def test_a_candidate_that_changes_during_the_run_is_rejected(project: GitRepo) -> None:
    def edit(_request: SuiteRequest, done: SuiteRun) -> SuiteRun:
        project.write("src/app/late.py", "x = 1\n")
        project.git("add", "-A")
        return done

    with pytest.raises(GitioError, match="changed from tree"):
        run(project, runner(Script(files=FULL), edit))


def test_a_native_pass_without_independent_runner_proof_fails(project: GitRepo) -> None:
    script = Script((("test_ok", Outcome.PASSED),), files=FULL)
    tests = runner(
        script, hook=lambda _, done: replace(done, invalidated=frozenset({"tests::test_ok"}))
    )
    report = run(project, tests)
    assert rules_of(report) == ["tests.failed"]
    assert "1 of 1 tests failed" in report.diagnostics[0].message
    assert report.coverage == ()
