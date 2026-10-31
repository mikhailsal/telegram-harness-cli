from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import pytest

from fakes import (
    BINARY,
    INHERITED,
    Commands,
    ExecutedError,
    Server,
    execve,
    manifest,
    no_network,
    release,
)
from quality_launcher import BOOT, Host, main, uv_environment
from quality_launcher_uv import Opener


@dataclass(frozen=True)
class World:
    core: Path
    cache: Path
    environ: dict[str, str]

    @property
    def uv(self) -> Path:
        return self.cache / "toolchains" / "uv-9.9.9" / "uv"

    @property
    def python(self) -> Path:
        return self.core / ".venv" / "bin" / "python"

    def install_uv(self) -> None:
        self.uv.parent.mkdir(parents=True)
        self.uv.write_bytes(BINARY)
        self.uv.chmod(0o755)

    def env(self) -> dict[str, str]:
        return uv_environment(self.environ, self.cache)

    def run(self, argv: list[str], calls: Commands, opener: Opener = no_network) -> ExecutedError:
        host = Host(self.environ, "x86_64", opener, calls, execve)
        with pytest.raises(ExecutedError) as executed:
            main(argv, self.core, host)
        return executed.value

    def fail(self, argv: list[str], commands: Commands) -> int:
        return main(argv, self.core, Host(self.environ, "x86_64", Server(), commands, execve))


@pytest.fixture
def world(tmp_path: Path) -> World:
    core = tmp_path / "core"
    (core / ".venv" / "bin").mkdir(parents=True)
    (core / "toolchains.toml").write_text(manifest(release()), encoding="utf-8")
    (core / ".python-version").write_text("3.14.8\n", encoding="utf-8")
    python = core / ".venv" / "bin" / "python"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)
    environ = {"HOME": str(tmp_path / "home"), "XDG_CACHE_HOME": str(tmp_path / "xdg"), **INHERITED}
    return World(core, tmp_path / "xdg" / "quality", environ)


def test_a_command_syncs_offline_and_replaces_the_launcher(world: World) -> None:
    world.install_uv()
    commands = Commands([0])
    executed = world.run(["check", "--rev", "HEAD"], commands)
    sync = [str(world.uv), "sync", "--frozen", "--offline", "--quiet"]
    assert [(run.argv, run.cwd, run.env, run.capture) for run in commands.runs] == [
        (sync, world.core, world.env(), True)
    ]
    python = str(world.python)
    assert executed.path == python
    assert executed.argv == [
        python,
        "-I",
        "-c",
        BOOT,
        str(world.core / "src"),
        "check",
        "--rev",
        "HEAD",
    ]
    assert executed.env == world.env()
    assert not set(INHERITED) & set(executed.env)


def test_a_command_without_uv_asks_for_bootstrap(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    commands = Commands([])
    assert world.fail(["check"], commands) == 3
    assert (
        capsys.readouterr().err == "quality: uv 9.9.9 is not installed; run ./quality bootstrap\n"
    )
    assert commands.runs == []


def test_an_offline_sync_failure_names_bootstrap_and_shows_uv_output(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    world.install_uv()
    commands = Commands([2], "error: Failed to fetch: `pytest`\n")
    assert world.fail(["check"], commands) == 3
    assert capsys.readouterr().err == (
        "quality: the core environment cannot be prepared offline; run ./quality bootstrap\n"
        "error: Failed to fetch: `pytest`\n"
    )


def test_a_missing_core_interpreter_asks_for_bootstrap(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    world.install_uv()
    world.python.unlink()
    assert world.fail(["check"], Commands([0])) == 3
    assert capsys.readouterr().err == (
        f"quality: the core interpreter {world.python} is missing; run ./quality bootstrap\n"
    )


@pytest.mark.parametrize("argv", [["bootstrap"], ["template", "export", "../project"]])
def test_bootstrap_installs_uv_python_and_the_environment(world: World, argv: list[str]) -> None:
    server = Server()
    commands = Commands([0, 0])
    executed = world.run(argv, commands, opener=server)
    assert world.uv.read_bytes() == BINARY
    uv = str(world.uv)
    downloads = {**world.env(), "UV_PYTHON_DOWNLOADS": "automatic"}
    assert [(run.argv, run.cwd, run.env, run.capture) for run in commands.runs] == [
        ([uv, "python", "install", "--no-bin", "3.14.8"], world.core, downloads, False),
        ([uv, "sync", "--frozen"], world.core, world.env(), False),
    ]
    assert executed.argv[-len(argv) :] == argv
    assert len(server.downloads) == 1


@pytest.mark.parametrize("argv", [["template"], ["export", "bootstrap"], ["check", "bootstrap"]])
def test_other_commands_never_download(
    world: World, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    assert world.fail(argv, Commands([])) == 3
    assert "is not installed; run ./quality bootstrap" in capsys.readouterr().err


def test_bootstrap_reuses_an_installed_uv(world: World) -> None:
    world.install_uv()
    assert world.run(["bootstrap"], Commands([0, 0])).argv[-1] == "bootstrap"


@pytest.mark.parametrize(
    ("statuses", "message"),
    [
        ([1], "quality: uv python install --no-bin 3.14.8 failed with exit status 1\n"),
        ([0, 4], "quality: uv sync --frozen failed with exit status 4\n"),
    ],
)
def test_bootstrap_stops_at_the_first_failed_step(
    world: World, capsys: pytest.CaptureFixture[str], statuses: list[int], message: str
) -> None:
    commands = Commands(list(statuses))
    assert world.fail(["bootstrap"], commands) == 3
    assert capsys.readouterr().err == message
    assert len(commands.runs) == len(statuses)


def test_bootstrap_on_an_unsupported_machine_downloads_nothing(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    host = Host(world.environ, "riscv64", no_network, Commands([]), execve)
    assert main(["bootstrap"], world.core, host) == 3
    assert capsys.readouterr().err == "quality: unsupported machine architecture: riscv64\n"


@pytest.mark.parametrize(
    ("manifest_bytes", "message"),
    [
        (None, "toolchains.toml is missing; the launcher must stay in tools/quality/launcher"),
        (b"[tools.uv]\nversion = 1\n", 'toolchains.toml line 2: expected key = "string"'),
        (b"\xff\xfe", "'utf-8' codec can't decode byte 0xff"),
    ],
)
def test_an_unusable_manifest_exits_with_an_environment_error(
    world: World, capsys: pytest.CaptureFixture[str], manifest_bytes: bytes | None, message: str
) -> None:
    path = world.core / "toolchains.toml"
    if manifest_bytes is None:
        path.unlink()
    else:
        path.write_bytes(manifest_bytes)
    assert world.fail(["check"], Commands([])) == 3
    assert message in capsys.readouterr().err


def test_a_failed_exec_exits_with_an_environment_error(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(path: str, argv: list[str], env: Mapping[str, str]) -> NoReturn:
        raise OSError(8, "Exec format error")

    world.install_uv()
    host = Host(world.environ, "x86_64", no_network, Commands([0]), broken)
    assert main(["check"], world.core, host) == 3
    assert capsys.readouterr().err == "quality: [Errno 8] Exec format error\n"
