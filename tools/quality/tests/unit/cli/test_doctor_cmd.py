import json
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.python.environment import Uv
from quality.cli import doctor_cmd
from quality.cli.main import main
from quality.diagnostics import Diagnostic
from quality.engine import Tools
from quality.hooks import HookName, hooks_directory, install
from quality.policy import Component, Profile
from quality.testkit.gitrepo import GitRepo
from quality.testkit.policies import policy_toml
from quality.toolchain import ToolchainError
from quality.toolchain.install import MISSING

UNCACHED = native.failure("uv sync --offline", "a package is not cached").diagnostic


@dataclass(slots=True)
class ScriptedSetup:
    """Probes nothing; records each component and fails when told to."""

    broken: bool = False
    calls: list[str] = field(default_factory=list[str])

    def provision(self, component: Component, root: Path) -> list[Diagnostic]:
        self.calls.append(f"provision {component.id}")
        return []

    def probe(self, component: Component, root: Path) -> list[Diagnostic]:
        self.calls.append(f"probe {component.id}")
        if self.broken:
            raise native.ToolFailedError(UNCACHED)
        return []


@pytest.fixture
def setup() -> ScriptedSetup:
    return ScriptedSetup()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setup: ScriptedSetup) -> GitRepo:
    bin_dir = tmp_path / "bin"

    def core_tool(name: str) -> Path:
        return bin_dir / name

    repo = GitRepo.create(tmp_path / "repo")
    repo.write("quality.toml", policy_toml())
    repo.write("README.md", "# Project\n")
    repo.write("src/app/__init__.py", "")
    repo.write("tests/test_app.py", "def test_ok():\n    pass\n")
    repo.git("add", "-A")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    tools = Tools(
        Path(sys.executable),
        bin_dir / "gitleaks",
        bin_dir / "gitleaks.toml",
        bin_dir / "actionlint",
    )
    monkeypatch.setattr(doctor_cmd, "local_uv", lambda: Uv(bin_dir / "uv", tmp_path / "py"))
    monkeypatch.setattr(doctor_cmd, "local_tools", lambda: tools)
    monkeypatch.setattr(doctor_cmd, "local_scanner", lambda: bin_dir / "osv-scanner")
    monkeypatch.setattr(doctor_cmd, "core_tool", core_tool)
    monkeypatch.setattr(doctor_cmd, "local_setups", lambda: {Profile.PYTHON: setup})
    monkeypatch.chdir(repo.path)
    return repo


def toolchain_lines(tmp_path: Path) -> list[str]:
    bin_dir = tmp_path / "bin"
    return [
        f"python {platform.python_version()}: {sys.executable}",
        *(
            f"{name}: {bin_dir / name}"
            for name in ("uv", "gitleaks", "actionlint", "osv-scanner", "ruff", "pyright")
        ),
    ]


def hook_path(repo: GitRepo, name: HookName) -> Path:
    return hooks_directory(repo.repository()) / str(name)


def test_a_ready_working_tree_passes(
    project: GitRepo, setup: ScriptedSetup, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install(project.repository(), HookName.PRE_COMMIT)
    status = project.git("status", "--porcelain")
    assert main(["doctor"]) == 0
    git, *lines = capsys.readouterr().out.splitlines()
    assert git.startswith("git ")
    assert lines == [
        *toolchain_lines(tmp_path),
        "app: the python environment is ready",
        "pre-commit hook: current",
        "pre-push hook: missing",
        "PASSED: 0 error(s), 0 warning(s)",
    ]
    assert setup.calls == ["probe app"]
    assert project.git("status", "--porcelain") == status
    assert not hook_path(project, HookName.PRE_PUSH).exists()


def test_a_missing_hook_and_environment_fail_as_json(
    project: GitRepo, setup: ScriptedSetup, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    setup.broken = True
    assert main(["doctor", "--json"]) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["command"] == "doctor"
    assert sorted(d["rule"] for d in report["diagnostics"]) == ["hooks.missing", "tools.failed"]
    assert report["checks"][1:] == [
        *toolchain_lines(tmp_path),
        "pre-commit hook: missing",
        "pre-push hook: missing",
    ]
    assert not hook_path(project, HookName.PRE_COMMIT).exists()


def test_missing_tools_are_reported_together(
    project: GitRepo, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def absent(name: str) -> Path:
        raise ToolchainError(MISSING.diagnostic(f"{name} is not installed"))

    def no_uv() -> object:
        raise ToolchainError(MISSING.diagnostic("uv is not installed"))

    monkeypatch.setattr(doctor_cmd, "core_tool", absent)
    monkeypatch.setattr(doctor_cmd, "local_setups", no_uv)
    install(project.repository(), HookName.PRE_COMMIT)
    assert main(["doctor"]) == 3
    output = capsys.readouterr().out
    for name in ("ruff", "pyright", "uv"):
        assert f"{name} is not installed (tools.missing)" in output
    assert "environment is ready" not in output
    assert "pre-commit hook: current\n" in output


def test_outside_a_repository_only_the_toolchain_is_checked(
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
    assert main(["doctor"]) == 3
    output = capsys.readouterr().out
    assert output.startswith("\n".join(toolchain_lines(tmp_path)) + "\n")
    assert "(git.failed)" in output
    assert "hook:" not in output
    assert setup.calls == []
