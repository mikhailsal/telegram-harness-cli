import tempfile
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.python.environment import Uv
from quality.adapters.python.provision import PythonSetup
from quality.cli.local import local_uv
from quality.testkit.policies import ComponentSpec, build_policy
from quality.testkit.pythonproject import python_project

(APP,) = build_policy(ComponentSpec(root="app")).components
# Logs each call, fails `python install` with the given status, and syncs like uv.
FAKE_UV = """\
#!/bin/sh
echo "$PWD $UV_PYTHON_DOWNLOADS $*" >> "{log}"
if [ "$1" = python ]; then
    {install}
fi
mkdir -p "$UV_PROJECT_ENVIRONMENT/bin" && touch "$UV_PROJECT_ENVIRONMENT/bin/python"
"""


def fake_uv(tmp_path: Path, install: str = "exit 0") -> PythonSetup:
    script = tmp_path / "uv"
    script.write_text(FAKE_UV.format(log=tmp_path / "uv.log", install=install))
    script.chmod(0o755)
    return PythonSetup(Uv(script, tmp_path / "pythons"))


def calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "uv.log"
    return log.read_text().splitlines() if log.exists() else []


def failure(component: Path, setup: PythonSetup) -> str:
    with pytest.raises(ToolFailedError) as raised:
        setup.provision(APP, component.parent)
    assert raised.value.diagnostic.rule == "tools.failed"
    return raised.value.diagnostic.message


def test_a_locked_project_is_probed_offline_in_a_throwaway_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, scratch = tmp_path / "snapshot", tmp_path / "scratch"
    python_project(snapshot / "app", {"src/app/__init__.py": ""})
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    assert PythonSetup(local_uv()).probe(APP, snapshot) == []
    assert list(scratch.iterdir()) == []
    assert not (snapshot / "app" / ".venv").exists()


def test_provision_installs_the_pinned_interpreter_then_syncs(
    component: Path, tmp_path: Path
) -> None:
    (component / ".python-version").write_text("\n3.14.8\n\n")
    assert fake_uv(tmp_path).provision(APP, component.parent) == []
    assert calls(tmp_path) == [
        f"{component} automatic python install --no-bin 3.14.8",
        f"{component} never sync --frozen --quiet",
    ]


def test_probe_only_syncs_offline(component: Path, tmp_path: Path) -> None:
    assert fake_uv(tmp_path).probe(APP, component.parent) == []
    assert calls(tmp_path) == [f"{component} never sync --frozen --quiet --offline"]


@pytest.mark.parametrize("remove", ["pyproject.toml", "uv.lock"])
def test_configuration_problems_are_reported_before_uv_runs(
    component: Path, tmp_path: Path, remove: str
) -> None:
    (component / remove).unlink()
    setup = fake_uv(tmp_path)
    for step in (setup.provision, setup.probe):
        found = step(APP, component.parent)
        assert [(d.rule, d.location and d.location.path) for d in found] == [
            ("python.config", f"app/{remove}")
        ]
    assert calls(tmp_path) == []


def test_a_failed_install_reports_what_uv_printed(component: Path, tmp_path: Path) -> None:
    setup = fake_uv(tmp_path, install='echo " no download for 3.14.8 " >&2; exit 2')
    assert failure(component, setup) == "uv python install: app: no download for 3.14.8"
    assert len(calls(tmp_path)) == 1


def test_a_silent_failed_install_reports_the_exit_status(component: Path, tmp_path: Path) -> None:
    assert failure(component, fake_uv(tmp_path, install="exit 5")) == "uv python install: app: 5"


def test_an_install_that_cannot_run_is_a_tool_failure(component: Path, tmp_path: Path) -> None:
    missing = PythonSetup(Uv(tmp_path / "missing-uv", tmp_path))
    message = failure(component, missing)
    assert message.startswith("uv python install: app: ")
    assert str(tmp_path / "missing-uv") in message
