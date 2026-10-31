import sys
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.python.fixer import PythonFixer
from quality.adapters.python.profile import PythonProfile
from quality.adapters.python.provision import PythonSetup
from quality.adapters.typescript.fixer import TypeScriptFixer
from quality.adapters.typescript.profile import TypeScriptProfile
from quality.adapters.typescript.provision import TypeScriptSetup
from quality.cli import local
from quality.cli.local import (
    core_tool,
    local_fixers,
    local_profiles,
    local_setups,
    local_tools,
    local_uv,
    node_environments,
    npm_cache,
    npm_pins,
    pinned,
    tool_step,
    worktree_files,
)
from quality.policy import Profile
from quality.testkit.gitrepo import GitRepo
from quality.toolchain import (
    ManifestError,
    Tool,
    ToolchainError,
    cache_root,
    load_manifest,
    python_root,
)
from quality.toolchain.install import MISSING


def missing(call: object) -> str:
    assert callable(call)
    with pytest.raises(ToolchainError) as raised:
        call()
    return raised.value.diagnostic.message


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitRepo:
    repo = GitRepo.create(tmp_path / "repo")
    repo.write("a.txt", "first\n")
    repo.commit_all("first")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(repo.home))
    monkeypatch.chdir(repo.path)
    return repo


def test_pinned_tools_come_from_the_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pinned("uv") == load_manifest().tools["uv"]
    assert missing(lambda: pinned("no-such-tool")) == "toolchain manifest: 'no-such-tool'"

    def broken() -> object:
        msg = "bad manifest"
        raise ManifestError(msg)

    monkeypatch.setattr(local, "load_manifest", broken)
    assert missing(lambda: pinned("uv")) == "toolchain manifest: bad manifest"


def test_the_common_gate_tools_are_located_in_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = local_tools()
    assert tools.python == Path(sys.executable)
    assert tools.gitleaks_rules.is_file()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert "gitleaks" in missing(local_tools)
    tool = pinned("gitleaks")
    binary = cache_root() / f"gitleaks-{tool.version}" / tool.binary
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    assert missing(local_tools).startswith("gitleaks rule set is not installed")
    (binary.parent / "gitleaks.toml").write_text("")
    assert missing(local_tools).startswith(f"actionlint {pinned('actionlint').version} is not")


def test_uv_is_located_with_its_managed_interpreters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uv = local_uv()
    assert uv.executable.is_file()
    assert uv.pythons == python_root()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert missing(local_uv).startswith(f"uv {pinned('uv').version} is not installed")


def test_core_tools_live_beside_the_core_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert core_tool("ruff") == Path(sys.executable).parent / "ruff"
    assert missing(lambda: core_tool("no-such-tool")).startswith("no-such-tool is not installed")
    (tmp_path / "ruff").write_text("not executable\n")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    expected = f"ruff is not installed in the core environment (expected {tmp_path / 'ruff'})"
    assert missing(lambda: core_tool("ruff")) == expected


def test_the_python_profile_uses_the_installed_tools() -> None:
    profiles = local_profiles()
    assert list(profiles) == [Profile.PYTHON, Profile.TYPESCRIPT, Profile.DART, Profile.PHP]
    python = profiles[Profile.PYTHON]
    assert isinstance(python, PythonProfile)
    assert (python.ruff, python.pyright) == (core_tool("ruff"), core_tool("pyright"))
    assert python.environments.uv == local_uv()
    setups, fixers = local_setups(), local_fixers()
    assert setups[Profile.PYTHON] == PythonSetup(local_uv())
    assert fixers[Profile.PYTHON] == PythonFixer(core_tool("ruff"))


def test_the_typescript_profile_uses_the_pinned_packages_and_the_npm_cache() -> None:
    profile = local_profiles()[Profile.TYPESCRIPT]
    setup = local_setups()[Profile.TYPESCRIPT]
    fixer = local_fixers()[Profile.TYPESCRIPT]
    assert isinstance(profile, TypeScriptProfile)
    assert isinstance(setup, TypeScriptSetup)
    assert isinstance(fixer, TypeScriptFixer)
    packages = load_manifest().packages
    assert (
        profile.pins == setup.pins == fixer.pins == {**packages["npm"], **packages["npm-browser"]}
    )
    assert npm_pins() == profile.pins
    assert npm_cache() == cache_root().parent / "npm" == setup.cache
    assert node_environments().cache == npm_cache()
    assert fixer.listing is worktree_files


def test_browser_components_get_the_pinned_chromium(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def found(name: str, tool: Tool, cache: Path) -> Path:
        called.append(f"{name} {tool.version}")
        return cache / name

    monkeypatch.setattr(local, "locate", found)
    monkeypatch.setattr(local, "install", found)
    setup = local_setups()[Profile.TYPESCRIPT]
    assert isinstance(setup, TypeScriptSetup)
    assert called == [f"uv {pinned('uv').version}"]
    assert setup.chromium.locate() == setup.chromium.install() == cache_root() / "chromium"
    assert called[1:] == ["chromium 156.0.8078.4"] * 2


def test_the_npm_pins_need_a_valid_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> object:
        msg = "bad manifest"
        raise ManifestError(msg)

    monkeypatch.setattr(local, "load_manifest", broken)
    assert missing(npm_pins) == "toolchain manifest: bad manifest"


def test_node_is_located_or_installed_only_when_used() -> None:
    called: list[tuple[str, Tool, Path]] = []

    def found(name: str, tool: Tool, cache: Path) -> Path:
        called.append((name, tool, cache))
        return cache / name

    step = tool_step(local.NODE, found)
    assert called == []
    assert step() == cache_root() / "node"
    assert called == [("node", pinned("node"), cache_root())]

    def absent(name: str, tool: Tool, cache: Path) -> Path:
        raise ToolchainError(MISSING.diagnostic(f"{name} {tool.version} is not installed"))

    with pytest.raises(ToolFailedError, match=r"node .* is not installed") as raised:
        tool_step(local.NODE, absent)()
    assert raised.value.diagnostic.rule == MISSING.id


def test_the_fixer_lists_the_working_tree(project: GitRepo) -> None:
    project.write(".gitignore", "ignored.txt\n")
    project.write("ignored.txt", "x\n")
    project.write("new.txt", "x\n")
    (project.path / "dir").mkdir()
    assert worktree_files(project.path) == [".gitignore", "a.txt", "new.txt"]
