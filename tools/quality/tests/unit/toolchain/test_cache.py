import platform
from pathlib import Path

import pytest

from quality.toolchain import cache_root
from quality.toolchain.install import machine
from quality.toolchain.manifest import Architecture


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"XDG_CACHE_HOME": "/xdg", "HOME": "/home/u"}, "/xdg/quality/toolchains"),
        ({"XDG_CACHE_HOME": "", "HOME": "/home/u"}, "/home/u/.cache/quality/toolchains"),
        ({"HOME": "/home/u"}, "/home/u/.cache/quality/toolchains"),
    ],
)
def test_cache_root_follows_xdg(environ: dict[str, str], expected: str) -> None:
    assert cache_root(environ) == Path(expected)


def test_cache_root_defaults_to_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", "/from/env")
    assert cache_root() == Path("/from/env/quality/toolchains")


def test_cache_root_without_home_uses_the_user_directory() -> None:
    assert cache_root({}) == Path.home() / ".cache" / "quality" / "toolchains"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("x86_64", Architecture.X86_64),
        ("AMD64", Architecture.X86_64),
        ("aarch64", Architecture.AARCH64),
        ("arm64", Architecture.AARCH64),
        ("riscv64", None),
    ],
)
def test_machine_names_are_normalized(
    monkeypatch: pytest.MonkeyPatch, name: str, expected: Architecture | None
) -> None:
    monkeypatch.setattr(platform, "machine", lambda: name)
    assert machine() is expected
