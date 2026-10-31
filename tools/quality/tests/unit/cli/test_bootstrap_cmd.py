from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quality.adapters import native
from quality.cli import bootstrap_cmd
from quality.cli.main import main
from quality.diagnostics import Diagnostic
from quality.hooks import HookName, HookStatus, hooks_directory, inspect
from quality.policy import Component, Profile
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import policy_toml
from quality.toolchain import ManifestError, Tool, ToolchainError, load_manifest
from quality.toolchain.install import INSTALL_FAILED, MISSING

FOREIGN = "#!/bin/sh\nexit 0\n"
OFFLINE = native.failure("uv sync", "no network").diagnostic
TOOLS = load_manifest().tools


@dataclass(slots=True)
class ScriptedSetup:
    """Provisions nothing; records each component and fails when told to."""

    broken: bool = False
    calls: list[str] = field(default_factory=list[str])

    def provision(self, component: Component, root: Path) -> list[Diagnostic]:
        self.calls.append(f"provision {component.id}")
        if self.broken:
            raise native.ToolFailedError(OFFLINE)
        return []

    def probe(self, component: Component, root: Path) -> list[Diagnostic]:
        self.calls.append(f"probe {component.id}")
        return []


def installed(name: str, tool: Tool, root: Path) -> Path:
    return root / f"{name}-{tool.version}" / tool.binary


@pytest.fixture
def setup() -> ScriptedSetup:
    return ScriptedSetup()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setup: ScriptedSetup) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml())
    repo.write("README.md", "# Project\n")
    repo.write("src/app/__init__.py", "")
    repo.write("tests/test_app.py", "def test_ok():\n    pass\n")
    repo.git("add", "-A")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(bootstrap_cmd, "install", installed)
    monkeypatch.setattr(bootstrap_cmd, "local_setups", lambda: {Profile.PYTHON: setup})
    monkeypatch.chdir(repo.path)
    return repo


def hook(repo: GitRepo, name: HookName) -> Path:
    return hooks_directory(repo.repository()) / str(name)


def tool_line(tmp_path: Path) -> str:
    root = tmp_path / "cache" / "quality" / "toolchains"
    return "".join(
        f"{name} {TOOLS[name].version}: {installed(name, TOOLS[name], root)}\n"
        for name in ("gitleaks", "actionlint", "osv-scanner")
    )


def test_tools_environments_and_the_hook_are_installed(
    project: GitRepo, setup: ScriptedSetup, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    staged, status = project.git("ls-files", "--stage"), project.git("status", "--porcelain")
    assert main(["bootstrap"]) == 0
    assert capsys.readouterr().out == (
        f"{tool_line(tmp_path)}app: the python environment is ready\n"
        f"pre-commit hook: {hook(project, HookName.PRE_COMMIT)}\n"
        "PASSED: 0 error(s), 0 warning(s)\n"
    )
    assert setup.calls == ["provision app"]
    assert inspect(project.repository(), HookName.PRE_COMMIT).status is HookStatus.CURRENT
    assert not hook(project, HookName.PRE_PUSH).exists()
    assert project.git("ls-files", "--stage") == staged
    assert project.git("status", "--porcelain") == status


def test_pre_push_is_optional_and_running_again_is_safe(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["bootstrap", "--pre-push"]) == 0
    assert main(["bootstrap", "--pre-push"]) == 0
    output = capsys.readouterr().out
    assert output.count(f"pre-push hook: {hook(project, HookName.PRE_PUSH)}\n") == 2
    repository = project.repository()
    assert [inspect(repository, name).status for name in HookName] == [HookStatus.CURRENT] * 2


def test_a_foreign_hook_is_reported_and_left_unchanged(
    project: GitRepo, capsys: pytest.CaptureFixture[str]
) -> None:
    path = hook(project, HookName.PRE_COMMIT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FOREIGN)
    assert main(["bootstrap"]) == 3
    output = capsys.readouterr().out
    assert "exists and is not managed by quality; it was left unchanged" in output
    assert "(hooks.conflict)" in output
    assert path.read_text() == FOREIGN


def test_failed_downloads_do_not_stop_the_rest(
    project: GitRepo,
    setup: ScriptedSetup,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unreachable(name: str, tool: Tool, root: Path) -> Path:
        raise ToolchainError(INSTALL_FAILED.diagnostic(f"{name}: network is unreachable"))

    monkeypatch.setattr(bootstrap_cmd, "install", unreachable)
    setup.broken = True
    assert main(["bootstrap"]) == 3
    output = capsys.readouterr().out
    assert "gitleaks: network is unreachable (tools.install-failed)" in output
    assert "actionlint: network is unreachable (tools.install-failed)" in output
    assert "uv sync: no network (tools.failed)" in output
    assert f"pre-commit hook: {hook(project, HookName.PRE_COMMIT)}\n" in output


def test_a_broken_manifest_is_reported(
    project: GitRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken() -> object:
        msg = "bad manifest"
        raise ManifestError(msg)

    monkeypatch.setattr(bootstrap_cmd, "load_manifest", broken)
    assert main(["bootstrap"]) == 3
    output = capsys.readouterr().out
    assert "toolchain manifest: bad manifest (tools.install-failed)" in output
    assert "app: the python environment is ready\n" in output


def test_missing_environment_tools_are_reported(
    project: GitRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def missing() -> object:
        raise ToolchainError(MISSING.diagnostic("uv is not installed"))

    monkeypatch.setattr(bootstrap_cmd, "local_setups", missing)
    assert main(["bootstrap"]) == 3
    output = capsys.readouterr().out
    assert "uv is not installed (tools.missing)" in output
    assert "environment is ready" not in output
    assert hook(project, HookName.PRE_COMMIT).is_file()


def test_outside_a_repository_only_tools_are_installed(
    project: GitRepo,
    setup: ScriptedSetup,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert main(["bootstrap"]) == 3
    output = capsys.readouterr().out
    assert output.startswith(tool_line(tmp_path))
    assert "(git.failed)" in output
    assert setup.calls == []
