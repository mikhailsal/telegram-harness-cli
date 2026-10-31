from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quality.adapters import native
from quality.catalog import build_registry
from quality.diagnostics import Diagnostic, Severity
from quality.engine.setup import SetupReport, Staged, probe_components, provision_components
from quality.policy import Component, Profile
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import ComponentSpec, policy_toml

RULES = tuple(rule.id for rule in build_registry())
APP = ComponentSpec(root="app")
WEB = ComponentSpec(id="web", root="web", profile="typescript")
STALE = native.FAILED.diagnostic("uv sync: app: package not cached")
NOTE = Diagnostic("typescript.browser-ready", Severity.INFO, "Chromium 156 starts")


@dataclass
class Setup:
    """A profile setup that records what it saw and returns or raises what it was given."""

    remaining: list[Diagnostic] = field(default_factory=list[Diagnostic])
    error: native.ToolFailedError | None = None
    calls: list[tuple[str, str, Path]] = field(default_factory=list[tuple[str, str, Path]])
    seen: list[str] = field(default_factory=list[str])

    def _step(self, step: str, component: Component, root: Path) -> list[Diagnostic]:
        self.calls.append((step, component.id, root))
        self.seen.extend(sorted(p.relative_to(root).as_posix() for p in root.rglob("*.py")))
        if self.error is not None:
            raise self.error
        return self.remaining

    def provision(self, component: Component, root: Path) -> list[Diagnostic]:
        return self._step("provision", component, root)

    def probe(self, component: Component, root: Path) -> list[Diagnostic]:
        return self._step("probe", component, root)


@pytest.fixture
def project(repo: GitRepo) -> GitRepo:
    repo.write("quality.toml", policy_toml(APP, WEB))
    repo.write("app/src/app/core.py", "x = 1\n")
    repo.write("app/tests/test_core.py", "")
    repo.write("web/src/index.ts", "")
    repo.write("web/tests/index.test.ts", "")
    repo.commit_all("base")
    return repo


def rules_of(report: SetupReport) -> list[str]:
    return [d.rule for d in report.diagnostics]


def test_provision_reads_the_staged_candidate_in_a_snapshot(project: GitRepo) -> None:
    project.write("app/src/app/staged.py", "")
    project.git("add", "app/src/app/staged.py")
    project.write("app/src/app/untracked.py", "")
    python = Setup()
    report = provision_components(Staged(project.repository()), {Profile.PYTHON: python}, RULES)
    assert report.lines == ["app: the python environment is ready"]
    assert [(d.rule, d.component) for d in report.diagnostics] == [("profiles.unavailable", "web")]
    assert "environment setup for it" in report.diagnostics[0].message
    ((step, component, root),) = python.calls
    assert (step, component) == ("provision", "app")
    assert root != project.path
    assert not root.exists()
    assert python.seen == [
        "app/src/app/core.py",
        "app/src/app/staged.py",
        "app/tests/test_core.py",
    ]


def test_probe_runs_the_probe_step(project: GitRepo) -> None:
    python, typescript = Setup(), Setup()
    setups = {Profile.PYTHON: python, Profile.TYPESCRIPT: typescript}
    report = probe_components(Staged(project.repository()), setups, RULES)
    assert report == SetupReport(
        [],
        ["app: the python environment is ready", "web: the typescript environment is ready"],
    )
    assert [call[:2] for call in python.calls + typescript.calls] == [
        ("probe", "app"),
        ("probe", "web"),
    ]


@pytest.mark.parametrize(
    ("python", "ready"),
    [
        (Setup([STALE]), False),
        (Setup(error=native.failure("uv", "boom")), False),
        (Setup([NOTE]), True),
    ],
)
def test_only_errors_keep_a_component_from_ready(
    project: GitRepo, python: Setup, *, ready: bool
) -> None:
    setups = {Profile.PYTHON: python, Profile.TYPESCRIPT: Setup()}
    report = probe_components(Staged(project.repository()), setups, RULES)
    assert rules_of(report) == [NOTE.rule if ready else "tools.failed"]
    app = ["app: the python environment is ready"] if ready else []
    assert report.lines == [*app, "web: the typescript environment is ready"]


HELPER = ComponentSpec(id="web-helper", root="helper", profile="typescript", role="helper")


@pytest.mark.parametrize("mode", ["project", "maintainer"])
def test_a_dormant_helper_is_set_up_only_once_it_is_active(repo: GitRepo, mode: str) -> None:
    repo.write("quality.toml", policy_toml(APP, HELPER, mode=mode))
    repo.write("app/src/app/core.py", "x = 1\n")
    repo.write("helper/src/index.ts", "")
    repo.commit_all("base")
    python, typescript = Setup(), Setup()
    setups = {Profile.PYTHON: python, Profile.TYPESCRIPT: typescript}
    report = provision_components(Staged(repo.repository()), setups, RULES)
    if mode == "project":
        assert report.lines == [
            "app: the python environment is ready",
            "web-helper: dormant, so its typescript tools wait",
        ]
        assert typescript.calls == []
    else:
        assert report.lines[1] == "web-helper: the typescript environment is ready"
    repo.write("helper/src/index.ts", "// changed\n")
    repo.git("add", "helper/src/index.ts")
    staged = probe_components(Staged(repo.repository()), setups, RULES)
    assert staged.lines[1] == "web-helper: the typescript environment is ready"
    assert typescript.calls[-1][:2] == ("probe", "web-helper")


EXAMPLE = ComponentSpec(id="example", root="example", profile="typescript", role="example")


def test_a_shipped_example_waits_beside_an_application_of_its_profile(project: GitRepo) -> None:
    project.write("quality.toml", policy_toml(APP, WEB, EXAMPLE))
    project.write("example/src/main.ts", "")
    project.commit_all("example")
    python, typescript = Setup(), Setup()
    setups = {Profile.PYTHON: python, Profile.TYPESCRIPT: typescript}
    report = provision_components(Staged(project.repository()), setups, RULES)
    assert report.lines == [
        "app: the python environment is ready",
        "web: the typescript environment is ready",
        "example: dormant, so its typescript tools wait",
    ]
    assert [call[:2] for call in typescript.calls] == [("provision", "web")]
    project.write("example/src/main.ts", "// changed\n")
    project.git("add", "example/src/main.ts")
    staged = probe_components(Staged(project.repository()), setups, RULES)
    assert staged.lines[2] == "example: the typescript environment is ready"


def test_an_alternate_index_is_the_candidate(project: GitRepo, tmp_path: Path) -> None:
    index = tmp_path / "index"
    project.git("read-tree", "HEAD", GIT_INDEX_FILE=str(index))
    project.git("rm", "-q", "--cached", "app/tests/test_core.py", GIT_INDEX_FILE=str(index))
    python = Setup()
    target = Staged(project.repository(), index)
    provision_components(target, {Profile.PYTHON: python, Profile.TYPESCRIPT: Setup()}, RULES)
    assert python.seen == ["app/src/app/core.py"]


def test_an_unusable_policy_is_reported_without_setting_up(project: GitRepo) -> None:
    project.git("rm", "-q", "--cached", "quality.toml")
    python = Setup()
    report = provision_components(Staged(project.repository()), {Profile.PYTHON: python}, RULES)
    assert rules_of(report) == ["policy.missing"]
    assert (report.lines, python.calls) == ([], [])


def test_a_git_failure_is_reported(project: GitRepo, tmp_path: Path) -> None:
    index = tmp_path / "index"
    index.write_bytes(b"not an index")
    python = Setup()
    report = probe_components(Staged(project.repository(), index), {Profile.PYTHON: python}, RULES)
    assert rules_of(report) == ["git.failed"]
    assert (report.lines, python.calls) == ([], [])


def test_reports_merge_in_order() -> None:
    first = SetupReport([STALE], ["one"])
    first.merge(SetupReport([STALE], ["two"]))
    assert first == SetupReport([STALE, STALE], ["one", "two"])
