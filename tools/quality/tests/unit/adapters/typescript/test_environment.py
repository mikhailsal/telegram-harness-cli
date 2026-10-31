from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.typescript.environment import (
    Environments,
    Node,
    npm_ci,
    package_directory,
    tool_script,
)
from quality.testkit.nodefakes import FakeNode, install_package, script


def test_node_runs_with_a_private_home_and_itself_first_on_path(node: Node, tmp_path: Path) -> None:
    home = tmp_path / "home"
    env = node.environ(home, {"CI": "1"})
    assert home.is_dir()
    assert env["HOME"] == str(home)
    assert env["CI"] == "1"
    assert env["NODE_DISABLE_COMPILE_CACHE"] == "1"
    assert env["PATH"].split(":")[0] == str(node.executable.parent)
    assert node.npm == node.executable.parent.parent / "lib/node_modules/npm/bin/npm-cli.js"


def test_npm_ci_installs_offline_with_empty_configuration(
    fake_node: FakeNode, tmp_path: Path
) -> None:
    directory = tmp_path / "app"
    directory.mkdir()
    home = tmp_path / "home"
    npm_ci(fake_node.node, directory, home, label="app")
    [[cwd, command, *flags]] = fake_node.calls()
    assert (Path(cwd), command) == (directory, "ci")
    assert "--ignore-scripts" in flags
    assert "--offline" in flags
    assert f"--cache={fake_node.node.cache}" in flags
    assert f"--userconfig={home / 'npmrc'}" in flags
    assert f"--globalconfig={home / 'npmrc-global'}" in flags
    assert (home / "npmrc").read_text() == (home / "npmrc-global").read_text() == ""


def test_npm_ci_downloads_when_online(fake_node: FakeNode, tmp_path: Path) -> None:
    npm_ci(fake_node.node, tmp_path, tmp_path / "home", label="app", offline=False)
    [[_, _, *flags]] = fake_node.calls()
    assert "--offline" not in flags


@pytest.mark.parametrize(
    ("body", "detail"),
    [("echo 'ENOTCACHED' >&2\nexit 1\n", "ENOTCACHED"), ("exit 3\n", "exit status 3")],
)
def test_a_failed_install_names_the_bootstrap_command(
    fake_node: FakeNode, tmp_path: Path, body: str, detail: str
) -> None:
    fake_node.npm(body)
    with pytest.raises(ToolFailedError) as raised:
        npm_ci(fake_node.node, tmp_path, tmp_path / "home", label="web")
    message = raised.value.diagnostic.message
    assert message.startswith(f"npm ci: web: {detail}")
    assert "run `./quality bootstrap`" in message


def test_a_node_that_cannot_start_fails_the_install(tmp_path: Path) -> None:
    node = Node(tmp_path / "missing" / "node", tmp_path / "cache")
    with pytest.raises(ToolFailedError, match=r"npm ci: web: .*executable not found"):
        npm_ci(node, tmp_path, tmp_path / "home", label="web")


def test_node_is_located_once_and_each_lock_installed_once_per_work_area(
    fake_node: FakeNode, tmp_path: Path
) -> None:
    located: list[Path] = []

    def locate() -> Path:
        located.append(fake_node.node.executable)
        return fake_node.node.executable

    environments = Environments(locate, fake_node.node.cache)
    lock, other = tmp_path / "a", tmp_path / "b"
    for directory in (lock, other):
        directory.mkdir()
    first, second = tmp_path / "work-1", tmp_path / "work-2"
    assert environments.install(lock, first, label="a") == fake_node.node
    environments.install(lock, first, label="a")
    environments.install(other, first, label="b")
    environments.install(lock, second, label="a")
    assert located == [fake_node.node.executable]
    assert [Path(call[0]) for call in fake_node.calls()] == [lock, other, lock]
    assert (first / "typescript-home" / "npm-1").is_dir()


def test_packages_resolve_upward_but_not_past_the_lock(tmp_path: Path) -> None:
    top = tmp_path / "repo"
    member = top / "packages" / "app"
    member.mkdir(parents=True)
    install_package(top, "prettier", "1", "exit 0\n")
    install_package(member, "eslint", "1", "exit 0\n")
    install_package(tmp_path, "vitest", "1", "exit 0\n")
    assert package_directory("prettier", member, top) == top / "node_modules" / "prettier"
    assert package_directory("eslint", member, top) == member / "node_modules" / "eslint"
    assert package_directory("vitest", member, top) is None
    assert package_directory("vitest", tmp_path / "elsewhere", top) is None


def test_the_pinned_tool_script_is_returned(tmp_path: Path) -> None:
    cli = install_package(tmp_path, "@scope/tool", "2.0.0", "exit 0\n")
    assert tool_script("@scope/tool", tmp_path, tmp_path, "2.0.0") == cli.resolve()
    package = tmp_path / "node_modules" / "single"
    script(package / "run.sh", "exit 0\n")
    (package / "package.json").write_text('{"version": "1.0.0", "bin": "run.sh"}')
    assert tool_script("single", tmp_path, tmp_path, "1.0.0") == (package / "run.sh").resolve()
    named = tmp_path / "node_modules" / "@scope" / "runner"
    script(named / "cli.sh", "exit 0\n")
    (named / "package.json").write_text('{"version": "3.0.0", "bin": {"run": "cli.sh"}}')
    found = tool_script("@scope/runner", tmp_path, tmp_path, "3.0.0", "run")
    assert found == (named / "cli.sh").resolve()


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ("{", "tool: .*package.json"),
        ('{"version": "1.0.0"}', "holds version '1.0.0', not 2.0.0"),
        ('{"version": "2.0.0"}', "has no command-line script"),
        ('{"version": "2.0.0", "bin": {"tool": 7}}', "has no command-line script"),
        ('{"version": "2.0.0", "bin": {"tool": "../escape.sh"}}', "has no command-line script"),
        ('{"version": "2.0.0", "bin": {"tool": "missing.sh"}}', "has no command-line script"),
    ],
)
def test_unusable_tool_packages_fail(tmp_path: Path, manifest: str, message: str) -> None:
    package = tmp_path / "node_modules" / "tool"
    package.mkdir(parents=True)
    script(tmp_path / "node_modules" / "escape.sh", "exit 0\n")
    (package / "package.json").write_text(manifest)
    with pytest.raises(ToolFailedError, match=message):
        tool_script("tool", tmp_path, tmp_path, "2.0.0")


def test_a_missing_tool_names_the_bootstrap_command(tmp_path: Path) -> None:
    with pytest.raises(ToolFailedError, match=r"tool is not installed for .*bootstrap"):
        tool_script("tool", tmp_path, tmp_path, "1.0.0")


def test_fake_npm_logs_nothing_before_it_runs(fake_node: FakeNode) -> None:
    assert fake_node.calls() == []
