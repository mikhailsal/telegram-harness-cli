import json
from pathlib import Path

import pytest

from quality.testkit import scratch, typescriptproject
from quality.testkit.nodefakes import PINS, FakeNode, script
from quality.testkit.scratch import create_project, typescript
from quality.testkit.typescriptproject import (
    lock,
    package_json,
    pinned_node,
    pinned_packages,
    typescript_project,
)


@pytest.fixture
def fake_node(tmp_path: Path) -> FakeNode:
    node = FakeNode.create(tmp_path / "fake-node", tmp_path / "npm-cache")
    node.npm('echo "{}" > package-lock.json\n')
    return node


def test_the_pinned_packages_come_from_the_manifest() -> None:
    assert pinned_packages() == PINS


def test_the_pinned_node_is_located_in_the_toolchain_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "toolchains"
    executable = script(root / "node-24.22.0" / "bin" / "node", "exit 0\n")
    monkeypatch.setattr(typescriptproject, "cache_root", lambda: root)
    found = pinned_node()
    assert found.executable == executable
    assert found.cache == tmp_path / "npm"


def test_package_manifests_choose_the_module_format() -> None:
    assert json.loads(package_json("web")) == {
        "name": "web",
        "version": "0.0.0",
        "private": True,
        "type": "module",
    }
    document = json.loads(package_json("cjs", module=False, workspaces=["packages/*"]))
    assert "type" not in document
    assert document["workspaces"] == ["packages/*"]


def test_lockfiles_are_written_offline_from_the_cache(fake_node: FakeNode, tmp_path: Path) -> None:
    directory = tmp_path / "web"
    directory.mkdir()
    assert lock(directory, fake_node.node) == directory / "package-lock.json"
    ((cwd, command, *flags),) = fake_node.calls()
    assert (cwd, command) == (str(directory), "install")
    assert {"--package-lock-only", "--offline", "--ignore-scripts"} <= set(flags)
    assert f"--cache={tmp_path / 'npm-cache'}" in flags


def test_npm_starts_from_the_helper_lockfile_or_the_existing_one(
    fake_node: FakeNode, tmp_path: Path
) -> None:
    fake_node.npm('cp package-lock.json start.json\necho "{}" > package-lock.json\n')
    lock(tmp_path, fake_node.node)
    assert (tmp_path / "start.json").read_text() == typescriptproject.SEED.read_text()
    (tmp_path / "package-lock.json").write_text("existing")
    lock(tmp_path, fake_node.node)
    assert (tmp_path / "start.json").read_text() == "existing"


def test_a_lockfile_npm_cannot_write_fails_the_arrangement(
    fake_node: FakeNode, tmp_path: Path
) -> None:
    fake_node.npm("echo 'no cached package' >&2\nexit 1\n")
    with pytest.raises(AssertionError, match=r"npm could not lock .*: no cached package"):
        lock(tmp_path, fake_node.node)


def test_components_are_written_complete(fake_node: FakeNode, tmp_path: Path) -> None:
    web = typescript_project(tmp_path / "web", node=fake_node.node)
    assert sorted(path.relative_to(web).as_posix() for path in web.rglob("*.*")) == [
        "eslint.config.js",
        "package-lock.json",
        "package.json",
        "src/core.ts",
        "tests/core.test.ts",
        "tsconfig.json",
    ]
    assert json.loads((web / "package.json").read_text())["devDependencies"] == PINS
    cjs = typescript_project(
        tmp_path / "cjs", {"src/a.ts": ""}, name="cjs", module=False, node=fake_node.node
    )
    assert (cjs / "eslint.config.mjs").is_file()
    assert (cjs / "src/a.ts").is_file()
    assert not (cjs / "src/core.ts").exists()


def test_scratch_projects_hold_typescript_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[tuple[Path, str]] = []

    def record(directory: Path, *, name: str) -> Path:
        written.append((directory, name))
        typescriptproject.write(directory, {"package.json": package_json(name)})
        return directory

    monkeypatch.setattr(scratch, "typescript_project", record)
    project = create_project(tmp_path / "project", typescript())
    assert written == [(project.path / "web", "web")]
    assert 'profile = "typescript"' in (project.path / "quality.toml").read_text()
    assert "node_modules/" in (project.path / ".gitignore").read_text()
    assert project.repo.git("status", "--porcelain") == ""
