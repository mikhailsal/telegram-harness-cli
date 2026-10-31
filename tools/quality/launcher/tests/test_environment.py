import os
import runpy
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import quality_launcher
from fakes import INHERITED
from quality_launcher import cache_base, python_version, run_command, uv_environment
from quality_launcher_uv import LauncherError

SCRIPT = Path(quality_launcher.__file__).resolve()
CORE = SCRIPT.parents[2]


def test_the_cache_follows_xdg_and_home() -> None:
    assert cache_base({"XDG_CACHE_HOME": "/xdg", "HOME": "/home/u"}) == Path("/xdg/quality")
    assert cache_base({"XDG_CACHE_HOME": "", "HOME": "/home/u"}) == Path("/home/u/.cache/quality")
    assert cache_base({}) == Path.home() / ".cache" / "quality"


def test_uv_runs_with_managed_python_only_and_without_inherited_environments() -> None:
    environ = {"PATH": "/bin", "UV_PYTHON_DOWNLOADS": "automatic", **INHERITED}
    assert uv_environment(environ, Path("/cache/quality")) == {
        "PATH": "/bin",
        "UV_PYTHON_INSTALL_DIR": "/cache/quality/python",
        "UV_PYTHON_PREFERENCE": "only-managed",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_NO_PROGRESS": "1",
    }


@pytest.mark.parametrize("content", [None, "\n"])
def test_the_python_pin_must_be_present(tmp_path: Path, content: str | None) -> None:
    if content is not None:
        (tmp_path / ".python-version").write_text(content, encoding="utf-8")
    with pytest.raises(LauncherError, match="must name the pinned CPython version"):
        python_version(tmp_path)


def test_the_python_pin_is_its_first_word(tmp_path: Path) -> None:
    (tmp_path / ".python-version").write_text("3.14.8  # pinned\n", encoding="utf-8")
    assert python_version(tmp_path) == "3.14.8"


def test_commands_capture_stdout_and_stderr_together(tmp_path: Path) -> None:
    code = "import os, sys; print(os.getcwd(), os.environ['MARK'], flush=True); sys.exit('bad')"
    status, output = run_command(
        [sys.executable, "-c", code], cwd=tmp_path, env={"MARK": "m"}, capture=True
    )
    assert (status, output) == (1, f"{tmp_path} m\nbad\n")


def test_uncaptured_commands_write_to_the_terminal(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    argv = [sys.executable, "-c", "print('visible')"]
    assert run_command(argv, cwd=tmp_path, env={}, capture=False) == (0, "")
    assert capfd.readouterr().out == "visible\n"


def test_commands_time_out(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(quality_launcher, "SUBPROCESS_TIMEOUT", 0.2)
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    with pytest.raises(LauncherError, match=r"timed out after 0\.2s"):
        run_command(argv, cwd=tmp_path, env={}, capture=True)


def uv_version() -> str:
    text = (CORE / "toolchains.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)["tools"]["uv"]["version"]


def test_the_script_runs_as_the_main_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["quality", "check"])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert exited.value.code == 3
    expected = f"quality: uv {uv_version()} is not installed; run ./quality bootstrap\n"
    assert capsys.readouterr().err == expected


def test_a_symbolic_link_to_the_script_finds_the_real_core(tmp_path: Path) -> None:
    link = tmp_path / "quality"
    link.symlink_to(SCRIPT)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "XDG_CACHE_HOME": ""}
    done = subprocess.run(
        [sys.executable, str(link), "check"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    expected = f"quality: uv {uv_version()} is not installed; run ./quality bootstrap\n"
    assert (done.returncode, done.stdout, done.stderr) == (3, "", expected)
