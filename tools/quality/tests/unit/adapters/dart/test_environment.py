from pathlib import Path

import pytest

from quality.adapters.dart.environment import (
    BOOTSTRAP_HINT,
    SDK_HINT,
    Environments,
    Sdk,
    located,
    pub_get,
)
from quality.adapters.native import ToolFailedError
from quality.testkit.dartfakes import FakeDart
from quality.toolchain.install import MISSING


def test_the_sdk_runs_with_analytics_off_a_private_home_and_the_shared_cache(
    tmp_path: Path, sdk: Sdk
) -> None:
    home = tmp_path / "home"
    env = sdk.environ(home, {"CI": "1", "HOME": str(tmp_path / "other")})
    assert home.is_dir()
    assert env["PATH"].split(":")[0] == str(sdk.executable.parent)
    assert env["PUB_CACHE"] == str(sdk.cache)
    assert env["DART_SUPPRESS_ANALYTICS"] == "true"
    assert (env["CI"], env["HOME"]) == ("1", str(tmp_path / "other"))
    assert sdk.environ(home)["HOME"] == str(home)
    assert sdk.command("format", "x") == (
        str(sdk.executable),
        "--suppress-analytics",
        "format",
        "x",
    )
    assert sdk.version_file == sdk.executable.parent.parent / "version"
    command = sdk.run_in(("a",), tmp_path, home, timeout=5.0)
    assert (command.argv, command.cwd, command.timeout, command.reap) == (
        ("a",),
        tmp_path,
        5.0,
        True,
    )


def test_pub_get_resolves_offline_or_downloads(tmp_path: Path, fake_dart: FakeDart) -> None:
    pub_get(fake_dart.sdk, tmp_path, tmp_path / "home", label="app")
    pub_get(fake_dart.sdk, tmp_path, tmp_path / "home", label="app", offline=False)
    flags = ["--suppress-analytics", "pub", "get", "--enforce-lockfile"]
    assert fake_dart.calls() == [[str(tmp_path), *flags, "--offline"], [str(tmp_path), *flags]]


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ("echo 'could not find package test' >&2\nexit 69\n", "could not find package test"),
        ("echo 'resolution failed'\nexit 1\n", "resolution failed"),
        ("exit 3\n", "exit status 3"),
    ],
)
def test_a_failed_resolution_names_the_bootstrap_command(
    tmp_path: Path, fake_dart: FakeDart, body: str, detail: str
) -> None:
    fake_dart.handle("pub", body)
    with pytest.raises(ToolFailedError) as raised:
        pub_get(fake_dart.sdk, tmp_path, tmp_path / "home", label="app")
    assert raised.value.diagnostic.message == f"dart pub get: app: {detail}; {BOOTSTRAP_HINT}"


def test_a_resolution_that_cannot_start_names_the_bootstrap_command(tmp_path: Path) -> None:
    sdk = Sdk(tmp_path / "missing" / "dart", tmp_path / "cache")
    with pytest.raises(ToolFailedError, match=r"dart pub get: app: .*run `./quality bootstrap`"):
        pub_get(sdk, tmp_path, tmp_path / "home", label="app")


def test_a_missing_sdk_names_the_bootstrap_command() -> None:
    def absent() -> Path:
        raise ToolFailedError(MISSING.diagnostic("dart 3.13.6 is not installed"))

    with pytest.raises(ToolFailedError) as raised:
        located(absent)
    assert raised.value.diagnostic.rule == MISSING.id
    assert raised.value.diagnostic.message == f"dart 3.13.6 is not installed; {SDK_HINT}"


def test_the_sdk_is_located_once_and_each_lock_resolved_once_per_work_area(
    tmp_path: Path, fake_dart: FakeDart
) -> None:
    located_calls: list[int] = []

    def locate() -> Path:
        located_calls.append(1)
        return fake_dart.sdk.executable

    environments = Environments(locate, fake_dart.sdk.cache)
    assert located_calls == []
    lock, work = tmp_path / "lock", tmp_path / "work"
    lock.mkdir()
    assert environments.install(lock, work, label="app") == fake_dart.sdk
    environments.install(lock, work, label="app")
    environments.install(lock, tmp_path / "other", label="app")
    assert environments.sdk() == fake_dart.sdk
    assert located_calls == [1]
    assert [call[0] for call in fake_dart.calls()] == [str(lock), str(lock)]
    assert sorted(path.name for path in (work / "dart-home").iterdir()) == ["pub-0"]
    assert (tmp_path / "other" / "dart-home" / "pub-1").is_dir()
