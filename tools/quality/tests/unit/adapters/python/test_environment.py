from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.python import environment
from quality.adapters.python.environment import (
    BOOTSTRAP_HINT,
    DOWNLOAD_TIMEOUT,
    SYNC_TIMEOUT,
    Environments,
    Sync,
    Uv,
    stale_lock,
    sync,
)
from quality.cli.local import local_uv
from quality.policy import Component
from quality.process import Command, Completed, run, sanitized
from quality.testkit.policies import ComponentSpec, build_policy
from quality.testkit.pythonproject import python_project

FAKE_UV = Uv(Path("/opt/uv"), Path("/opt/pythons"))
# Records each sync's directory and arguments and creates the interpreter where uv would.
RECORDING_UV = """\
#!/bin/sh
echo "$PWD $*" >> "{log}"
mkdir -p "$UV_PROJECT_ENVIRONMENT/bin" && touch "$UV_PROJECT_ENVIRONMENT/bin/python"
"""


def component(root: str = "app") -> Component:
    (found,) = build_policy(ComponentSpec(root=root)).components
    return found


def script(path: Path, body: str) -> Path:
    path.write_text(body if body.startswith("#!") else f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


def sync_failure(tmp_path: Path, uv: Uv) -> str:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    with pytest.raises(native.ToolFailedError) as raised:
        sync(uv, project, tmp_path / "env", label="app")
    return raised.value.diagnostic.message


def test_uv_uses_only_installed_managed_interpreters() -> None:
    base = {"HOME": "/home/u", "UV_CACHE_DIR": "/cache", "UV_INDEX_URL": "x", "PYTHONPATH": "/p"}
    env = FAKE_UV.environ(base=base)
    assert env["HOME"] == "/home/u"
    assert env["UV_CACHE_DIR"] == "/cache"
    assert env["UV_PYTHON_INSTALL_DIR"] == "/opt/pythons"
    assert (env["UV_PYTHON_PREFERENCE"], env["UV_PYTHON_DOWNLOADS"]) == ("only-managed", "never")
    assert env["UV_NO_PROGRESS"] == "1"
    assert not {"UV_INDEX_URL", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT"} & set(env)


def test_uv_targets_the_project_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    env = FAKE_UV.environ(project_environment=Path("/work/env"))
    assert env["UV_PROJECT_ENVIRONMENT"] == "/work/env"
    assert "UV_CACHE_DIR" not in env
    monkeypatch.setenv("UV_CACHE_DIR", "/elsewhere")
    assert FAKE_UV.environ()["UV_CACHE_DIR"] == "/elsewhere"


def test_a_locked_project_syncs_offline(tmp_path: Path) -> None:
    project = python_project(tmp_path / "app", {})
    python = sync(local_uv(), project, tmp_path / "env", label="app")
    assert python == tmp_path / "env" / "bin" / "python"
    code = "import coverage, pytest, pytest_cov; print(pytest.__version__)"
    done = run(Command((str(python), "-I", "-c", code), cwd=tmp_path, timeout=60, env=sanitized()))
    assert done.ok, done.stderr_text()


def test_a_compiled_environment_holds_the_bytecode_of_its_packages(tmp_path: Path) -> None:
    project = python_project(tmp_path / "app", {})
    sync(local_uv(), project, tmp_path / "plain", label="app")
    sync(local_uv(), project, tmp_path / "compiled", label="app", mode=Sync.CHECK)
    assert not list((tmp_path / "plain").rglob("pytest/__pycache__/*.pyc"))
    assert list((tmp_path / "compiled").rglob("pytest/__pycache__/*.pyc"))
    assert not list(project.rglob("__pycache__"))


def test_checks_sync_offline_and_bootstrap_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Command] = []
    target = tmp_path / "env"

    def fake(command: Command) -> Completed:
        seen.append(command)
        (target / "bin").mkdir(parents=True, exist_ok=True)
        (target / "bin" / "python").write_text("")
        return Completed(command.argv, 0, b"", b"", 0.0)

    monkeypatch.setattr(environment, "run", fake)
    sync(FAKE_UV, tmp_path, target, label="app")
    sync(FAKE_UV, tmp_path, target, label="app", mode=Sync.DOWNLOAD)
    sync(FAKE_UV, tmp_path, target, label="app", mode=Sync.CHECK)
    offline, online, compiled = seen
    assert offline.argv == ("/opt/uv", "sync", "--frozen", "--quiet", "--offline")
    assert online.argv == ("/opt/uv", "sync", "--frozen", "--quiet")
    assert compiled.argv == (*offline.argv, "--compile-bytecode")
    assert (offline.timeout, online.timeout) == (SYNC_TIMEOUT, DOWNLOAD_TIMEOUT)
    assert (offline.cwd, offline.env["UV_PROJECT_ENVIRONMENT"]) == (tmp_path, str(target))


def test_a_failed_sync_names_the_bootstrap_command(tmp_path: Path) -> None:
    uv = Uv(script(tmp_path / "uv", 'echo "package not cached" >&2\nexit 2'), tmp_path)
    assert sync_failure(tmp_path, uv) == f"uv sync: app: package not cached; {BOOTSTRAP_HINT}"


def test_a_silent_failure_reports_the_exit_status(tmp_path: Path) -> None:
    uv = Uv(script(tmp_path / "uv", "exit 3"), tmp_path)
    assert sync_failure(tmp_path, uv) == f"uv sync: app: exit status 3; {BOOTSTRAP_HINT}"


def test_a_sync_without_an_interpreter_fails(tmp_path: Path) -> None:
    uv = Uv(script(tmp_path / "uv", "exit 0"), tmp_path)
    expected = f"uv sync: app: no interpreter at {tmp_path / 'env' / 'bin' / 'python'}"
    assert sync_failure(tmp_path, uv) == expected


def test_a_missing_uv_fails(tmp_path: Path) -> None:
    message = sync_failure(tmp_path, Uv(tmp_path / "missing-uv", tmp_path))
    assert message.startswith("uv sync: app: ")
    assert message.endswith(BOOTSTRAP_HINT)


def test_environments_sync_once_per_component_and_work_area(tmp_path: Path) -> None:
    log = tmp_path / "syncs.log"
    uv = Uv(script(tmp_path / "uv", RECORDING_UV.format(log=log)), tmp_path)
    snapshot, first, second = tmp_path / "snapshot", tmp_path / "first", tmp_path / "second"
    (snapshot / "app").mkdir(parents=True)
    environments = Environments(uv)
    app = component()
    assert environments.location(app, first) == first / "python-env" / "app"
    python = environments.python(app, snapshot, first)
    assert python == first / "python-env" / "app" / "bin" / "python"
    assert environments.python(app, snapshot, first) == python
    assert (
        environments.python(app, snapshot, second)
        == second / "python-env" / "app" / "bin" / "python"
    )
    synced = f"{snapshot / 'app'} sync --frozen --quiet --offline --compile-bytecode"
    assert log.read_text().splitlines() == [synced] * 2


def test_a_lockfile_that_no_longer_matches_its_project_is_stale(tmp_path: Path) -> None:
    project = python_project(tmp_path / "app", {})
    assert stale_lock(local_uv(), project) is None
    pyproject = project / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace("dependencies = []", 'dependencies = ["idna"]')
    )
    # Offline, uv either finds the lock outdated or cannot resolve the new requirement
    # from the cache, which holds locked wheels but no index pages; both are stale.
    stale = stale_lock(local_uv(), project)
    assert stale is not None
    assert stale.startswith("error: ")


def test_a_lock_check_reports_uv_output_and_fails_on_errors(tmp_path: Path) -> None:
    quiet = Uv(script(tmp_path / "quiet", "exit 1"), tmp_path)
    assert stale_lock(quiet, tmp_path) == "uv.lock needs to be updated"
    broken = Uv(script(tmp_path / "broken", 'echo "no interpreter" >&2\nexit 2'), tmp_path)
    with pytest.raises(native.ToolFailedError, match="uv lock --check: exit status 2: no interp"):
        stale_lock(broken, tmp_path)
