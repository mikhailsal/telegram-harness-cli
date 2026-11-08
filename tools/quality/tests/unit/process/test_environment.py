import os
from pathlib import Path

import pytest

from quality.process import sanitized
from quality.process.environment import FIXED, SYSTEM_PATH


def test_only_allowlisted_variables_are_inherited() -> None:
    base = {
        "HOME": "/home/u",
        "https_proxy": "http://proxy",
        "GIT_DIR": "/elsewhere",
        "GIT_INDEX_FILE": "/x/index",
        "PYTHONPATH": "/injected",
        "NODE_OPTIONS": "--require=/evil.js",
        "PHP_INI_SCAN_DIR": "/evil",
        "PATH": "/attacker/bin",
    }
    env = sanitized(base)
    assert env["HOME"] == "/home/u"
    assert env["https_proxy"] == "http://proxy"
    for name in ("GIT_DIR", "GIT_INDEX_FILE", "PYTHONPATH", "NODE_OPTIONS", "PHP_INI_SCAN_DIR"):
        assert name not in env
    assert "/attacker/bin" not in env["PATH"]


def test_fixed_values_override_inherited_locale() -> None:
    env = sanitized({"LANG": "fr_FR.UTF-8", "TZ": "Europe/Paris"})
    for key, value in FIXED.items():
        assert env[key] == value


def test_tool_paths_precede_system_paths() -> None:
    env = sanitized({}, path=[Path("/opt/tool/bin"), Path("/opt/other/bin")])
    assert env["PATH"].split(os.pathsep) == ["/opt/tool/bin", "/opt/other/bin", *SYSTEM_PATH]


def test_extra_variables_are_added_but_path_is_reserved() -> None:
    assert sanitized({}, extra={"PUB_CACHE": "/c"})["PUB_CACHE"] == "/c"
    with pytest.raises(ValueError, match="PATH must be provided"):
        sanitized({}, extra={"PATH": "/bin"})


def test_defaults_to_the_current_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/from/os/environ")
    monkeypatch.setenv("GIT_WORK_TREE", "/elsewhere")
    env = sanitized()
    assert env["HOME"] == "/from/os/environ"
    assert "GIT_WORK_TREE" not in env
