import shutil
from pathlib import Path

import pytest

from quality.hooks import HookName, script
from quality.hooks.scripts import is_managed
from quality.process import Command, run, sanitized


@pytest.mark.parametrize("name", list(HookName))
def test_scripts_are_valid_posix_shell(tmp_path: Path, name: HookName) -> None:
    hook = tmp_path / str(name)
    hook.write_text(script(name))
    checks = [("sh", "-n", str(hook))]
    # ShellCheck is optional; when it is installed it must accept the scripts as POSIX sh.
    if shutil.which("shellcheck", path=sanitized()["PATH"]) is not None:
        checks.append(("shellcheck", "--shell=sh", str(hook)))
    for argv in checks:
        result = run(Command(argv, cwd=tmp_path, timeout=30, env=sanitized()))
        assert result.returncode == 0, result.stdout_text() + result.stderr_text()


@pytest.mark.parametrize("name", list(HookName))
def test_scripts_carry_the_marker_and_fail_fast(name: HookName) -> None:
    lines = script(name).splitlines()
    assert lines[0] == "#!/bin/sh"
    assert lines[1] == f"# quality-managed-hook: {name} v1"
    assert "set -eu" in lines
    assert f'local_hook="$(dirname "$0")/{name}.local"' in lines
    assert is_managed(name, script(name).encode())


def test_the_pre_commit_hook_only_checks_the_staged_candidate() -> None:
    assert script(HookName.PRE_COMMIT).endswith("exec ./quality check --staged\n")
    assert "git add" not in script(HookName.PRE_COMMIT)


def test_the_marker_names_the_hook() -> None:
    assert not is_managed(HookName.PRE_PUSH, script(HookName.PRE_COMMIT).encode())
    assert not is_managed(HookName.PRE_COMMIT, b"#!/bin/sh\n")
    assert not is_managed(HookName.PRE_COMMIT, b"# quality-managed-hook: pre-commit v1\n")
