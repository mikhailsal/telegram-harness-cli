import json
from datetime import date
from pathlib import Path

import pytest

from quality.catalog import build_registry
from quality.cli.main import command_table
from quality.diagnostics import Location
from quality.engine import Report, Settings, check_candidate
from quality.gitio import staged
from quality.policy import Profile, Suite
from quality.scenarios.reconcile import ScenarioSummary
from quality.testkit.candidates import pinned_tools
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, policy_toml, quarantine_toml
from quality.testkit.scenariorepo import FLOWS, SMOKE, SPEC, scenario_repo, scenario_runner
from quality.testkit.scenarios import ScenarioSpec, manifest_toml
from quality.testkit.suites import ScriptedRunner

TODAY = date(2026, 10, 20)
RULES = {rule.id: rule for rule in build_registry()}


@pytest.fixture
def project(tmp_path: Path) -> GitRepo:
    return scenario_repo(tmp_path / "repo")


def check(repo: GitRepo, tests: ScriptedRunner) -> Report:
    git = repo.repository()
    settings = Settings(
        tools=pinned_tools(),
        commands=command_table(),
        rules=RULES,
        today=TODAY,
        profiles={Profile.PYTHON: tests},
    )
    return check_candidate(git, staged(git), settings)


def found(report: Report) -> list[tuple[str, str]]:
    return [(d.rule, d.message) for d in report.diagnostics]


def suites_run(tests: ScriptedRunner) -> list[Suite]:
    return [request.suite for request in tests.requests]


def test_a_component_with_capabilities_needs_a_manifest(project: GitRepo) -> None:
    spec = ComponentSpec(capabilities=("cli",), integration=("integration",), e2e=("e2e",))
    project.write("quality.toml", policy_toml(spec, extra=quarantine_toml("x", "y", suite="e2e")))
    project.git("add", "-A")
    tests = scenario_runner()
    report = check(project, tests)
    policy = Location("quality.toml")
    assert [(d.rule, d.message, d.location) for d in report.diagnostics] == [
        (
            "scenarios.reference",
            "the quarantine of 'x' names 'y', which is not a scenario of the e2e suite of 'app'",
            policy,
        ),
        ("scenarios.manifest", "'app' declares cli but names no scenario manifest", policy),
    ]
    assert suites_run(tests) == [Suite.UNIT]


TOO_LARGE = "the scenario manifest 'scenarios.toml' is not a file of at most 1 MiB"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            b"schema_version = \n",
            [("config.syntax", "Invalid value"), ("scenarios.manifest", "Invalid value")],
        ),
        (
            b" " * (1024 * 1024 + 1),
            [
                ("files.blob-size", "blob is 1048577 bytes; the limit is 1048576 bytes"),
                ("scenarios.manifest", TOO_LARGE),
            ],
        ),
        (None, [("scenarios.manifest", TOO_LARGE)]),
    ],
)
def test_an_unreadable_manifest_is_a_usage_error(
    project: GitRepo, content: bytes | None, expected: list[tuple[str, str]]
) -> None:
    if content is None:
        project.git("rm", "-q", "--cached", "scenarios.toml")
    else:
        project.write("scenarios.toml", content)
        project.git("add", "-A")
    tests = scenario_runner()
    report = check(project, tests)
    assert found(report) == expected
    # Without a manifest only the unit suite runs, and nothing is reconciled.
    assert suites_run(tests) == [Suite.UNIT]
    assert report.scenarios == ()


HTTP = ComponentSpec(
    capabilities=("http-api",),
    integration=("integration",),
    scenarios="scenarios.toml",
)
LIST = ScenarioSpec(
    "list",
    ("tests::flow",),
    "http-api",
    "integration",
    requirements=("http-api.success", "http-api.contract"),
    operations=("listNotes",),
)
EXCUSED = {
    "http-api.invalid-input": "no input",
    "http-api.not-found": "no lookups",
    "http-api.permission": "public",
}
OPENAPI = {"openapi": "3.1.0", "paths": {"/notes": {"get": {"operationId": "listNotes"}}}}


@pytest.fixture
def service(project: GitRepo) -> GitRepo:
    project.git("rm", "-q", "-r", "-f", "e2e")
    project.write("quality.toml", policy_toml(HTTP))
    manifest = manifest_toml(LIST, openapi="api/openapi.json", not_applicable=EXCUSED)
    project.write("scenarios.toml", manifest)
    project.write("api/openapi.json", json.dumps(OPENAPI))
    project.git("add", "quality.toml", "scenarios.toml", "api")
    return project


def test_openapi_operations_are_read_from_the_snapshot(service: GitRepo) -> None:
    report = check(service, scenario_runner())
    assert found(report) == []
    assert report.scenarios == (ScenarioSummary("app", 1, 1),)
    paths = {"/notes": {"get": {"operationId": "listNotes"}, "post": {}}}
    service.write("api/openapi.json", json.dumps({**OPENAPI, "paths": paths}))
    service.git("add", "-A")
    assert found(check(service, scenario_runner())) == [
        ("scenarios.uncovered", "no scenario names the OpenAPI operation 'POST /notes'")
    ]


def test_a_missing_or_unusable_openapi_document_is_a_usage_error(service: GitRepo) -> None:
    service.write("api/openapi.json", "[]")
    service.git("add", "-A")
    assert found(check(service, scenario_runner())) == [
        (
            "scenarios.manifest",
            (
                "the OpenAPI document 'api/openapi.json' is unusable: openapi.json does not hold "
                "a JSON object"
            ),
        )
    ]
    service.git("rm", "-q", "--cached", "api/openapi.json")
    assert found(check(service, scenario_runner())) == [
        (
            "scenarios.manifest",
            "the OpenAPI document 'api/openapi.json' is not a file of the candidate",
        )
    ]


def test_a_scenario_of_the_base_revision_must_be_kept_or_retired(project: GitRepo) -> None:
    project.git("commit", "-q", "-m", "base")
    project.write("scenarios.toml", manifest_toml(SMOKE))
    project.git("add", "-A")
    assert found(check(project, scenario_runner())) == [
        (
            "scenarios.uncovered",
            "no scenario proves 'cli.flows': declared critical flows through the command line",
        ),
        (
            "scenarios.deleted",
            "scenario 'flows' of the base revision is neither in the manifest nor retired",
        ),
    ]
    project.write("scenarios.toml", manifest_toml(SMOKE, FLOWS, retired={"old": "gone"}))
    project.git("add", "-A")
    assert found(check(project, scenario_runner())) == []


GONE = manifest_toml(ScenarioSpec("gone", ("tests::x",), "cli", "e2e")).encode()
NO_MANIFEST = ComponentSpec(capabilities=("cli",), integration=("integration",), e2e=("e2e",))


@pytest.mark.parametrize(
    ("policy", "manifest"),
    [
        (b"", GONE),
        (b"schema_version = \n", GONE),
        (policy_toml(NO_MANIFEST).encode(), GONE),
        (policy_toml(SPEC).encode(), b"not toml ="),
        (policy_toml(SPEC).encode(), b" " * (1024 * 1024 + 1)),
    ],
    ids=["no-policy", "invalid-policy", "no-manifest", "invalid-manifest", "oversized-manifest"],
)
def test_an_unreadable_base_revision_has_no_scenarios(
    project: GitRepo, policy: bytes, manifest: bytes
) -> None:
    project.write("scenarios.toml", manifest)
    project.write("quality.toml", policy)
    project.git("add", "-A")
    if not policy:
        project.git("rm", "-q", "--cached", "quality.toml")
    project.git("commit", "-q", "-m", "base", "--no-verify")
    project.write("quality.toml", policy_toml(SPEC))
    project.write("scenarios.toml", manifest_toml(SMOKE, FLOWS))
    project.git("add", "-A")
    assert found(check(project, scenario_runner())) == []
