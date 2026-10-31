#!/usr/bin/env python3
"""Prepare the pinned quality core and run it.

The repository's `quality` symlink points here. This file runs on the system python3
(3.10 or newer) with the standard library only. `bootstrap` installs the pinned uv,
CPython, and core environment, and so does `template export`, which a fresh clone of
the template runs before any project exists; every other command syncs that
environment offline. All then replace this process with the core.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Protocol

from quality_launcher_uv import (
    LauncherError,
    Opener,
    executable,
    https_open,
    install_uv,
    load_uv,
    machine,
)

ENVIRONMENT_ERROR = 3
SUBPROCESS_TIMEOUT = 600.0
DROPPED = frozenset({"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "UV_PROJECT_ENVIRONMENT"})
BOOT = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); sys.argv[0] = 'quality'; "
    "from quality.cli.main import main; raise SystemExit(main())"
)

# The commands that may download the core environment (decisions 0010 and 0016).
DOWNLOADING = (("bootstrap",), ("template", "export"))

Exec = Callable[[str, list[str], Mapping[str, str]], NoReturn]


class Call(Protocol):
    """Run a command; return its exit status and, when captured, its output."""

    def __call__(
        self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], capture: bool
    ) -> tuple[int, str]:
        """Run `argv` in `cwd` with exactly `env`."""
        ...


def cache_base(environ: Mapping[str, str]) -> Path:
    """Return `$XDG_CACHE_HOME/quality`, defaulting to `~/.cache/quality`."""
    home = Path(environ.get("HOME", str(Path.home())))
    return Path(environ.get("XDG_CACHE_HOME") or str(home / ".cache")) / "quality"


def uv_environment(environ: Mapping[str, str], cache: Path) -> dict[str, str]:
    """Return the environment of every uv call and of the core."""
    env = {key: value for key, value in environ.items() if key not in DROPPED}
    env["UV_PYTHON_INSTALL_DIR"] = str(cache / "python")
    env["UV_PYTHON_PREFERENCE"] = "only-managed"
    env["UV_PYTHON_DOWNLOADS"] = "never"
    env["UV_NO_PROGRESS"] = "1"
    return env


def run_command(
    argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], capture: bool
) -> tuple[int, str]:
    """Run a command without a shell; capture stdout and stderr together if asked."""
    stream = subprocess.PIPE if capture else None
    try:
        done = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(env),
            timeout=SUBPROCESS_TIMEOUT,
            check=False,
            stdout=stream,
            stderr=subprocess.STDOUT if capture else None,
        )
    except subprocess.TimeoutExpired as error:
        msg = f"{argv[0]} timed out after {SUBPROCESS_TIMEOUT:g}s"
        raise LauncherError(msg) from error
    output: bytes = done.stdout or b""
    return done.returncode, output.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class Host:
    """The process environment and the effects the launcher uses."""

    environ: Mapping[str, str]
    arch: str
    opener: Opener = https_open
    call: Call = run_command
    execve: Exec = os.execve


def python_version(core: Path) -> str:
    """Return the CPython version pinned in the core's `.python-version`."""
    path = core / ".python-version"
    words = path.read_text(encoding="utf-8").split() if path.is_file() else []
    if not words:
        msg = f"{path} must name the pinned CPython version"
        raise LauncherError(msg)
    return words[0]


def _run(host: Host, argv: list[str], core: Path, env: Mapping[str, str]) -> None:
    if (status := host.call(argv, cwd=core, env=env, capture=False)[0]) != 0:
        msg = f"uv {' '.join(argv[1:])} failed with exit status {status}"
        raise LauncherError(msg)


def may_download(argv: Sequence[str]) -> bool:
    """Whether the command may download uv, CPython, and the core's locked packages."""
    return any(tuple(argv[: len(command)]) == command for command in DOWNLOADING)


def prepare(argv: Sequence[str], core: Path, host: Host) -> dict[str, str]:
    """Bring the core environment in line with its lockfile, downloading only if allowed."""
    uv = load_uv(core / "toolchains.toml")
    cache = cache_base(host.environ)
    env = uv_environment(host.environ, cache)
    if may_download(argv):
        tool = str(install_uv(uv, cache, machine(host.arch), host.opener))
        downloads = {**env, "UV_PYTHON_DOWNLOADS": "automatic"}
        # --no-bin keeps the interpreter out of the user's ~/.local/bin.
        install = [tool, "python", "install", "--no-bin", python_version(core)]
        _run(host, install, core, downloads)
        _run(host, [tool, "sync", "--frozen"], core, env)
        return env
    if not executable(path := uv.path(cache)):
        msg = f"uv {uv.version} is not installed; run ./quality bootstrap"
        raise LauncherError(msg)
    command = [str(path), "sync", "--frozen", "--offline", "--quiet"]
    status, output = host.call(command, cwd=core, env=env, capture=True)
    if status != 0:
        msg = f"the core environment cannot be prepared offline; run ./quality bootstrap\n{output}"
        raise LauncherError(msg.rstrip())
    return env


def launch(argv: Sequence[str], core: Path, host: Host) -> NoReturn:
    """Prepare the core for `argv` and replace this process with it."""
    env = prepare(argv, core, host)
    if not executable(python := core / ".venv" / "bin" / "python"):
        msg = f"the core interpreter {python} is missing; run ./quality bootstrap"
        raise LauncherError(msg)
    host.execve(str(python), [str(python), "-I", "-c", BOOT, str(core / "src"), *argv], env)


def main(argv: Sequence[str], core: Path, host: Host) -> int:
    """Run the launcher; return the exit status of an environment failure."""
    try:
        launch(argv, core, host)
    except (LauncherError, OSError, UnicodeError) as error:
        print(f"quality: {error}", file=sys.stderr)
    return ENVIRONMENT_ERROR


if __name__ == "__main__":
    HOST = Host(environ=os.environ, arch=platform.machine())
    sys.exit(main(sys.argv[1:], Path(__file__).resolve().parents[2], HOST))
