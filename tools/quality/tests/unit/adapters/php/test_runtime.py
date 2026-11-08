from collections.abc import Callable
from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.php import runtime
from quality.adapters.php.runtime import Php, Requirements, Runtime, located, probed, system_php
from quality.testkit.phpfakes import REQUIRED, FakePhp, probe


def failure(call: Callable[[], object]) -> str:
    with pytest.raises(native.ToolFailedError) as raised:
        call()
    return raised.value.diagnostic.message


def test_commands_send_errors_to_stderr_and_load_xdebug_only_for_coverage(php: Php) -> None:
    executable = str(php.executable)
    assert php.command("x.php") == (executable, "-d", "display_errors=stderr", "x.php")
    assert php.command("x.php", coverage=True) == php.command("x.php")
    unloaded = Php(php.executable, php.version, False, php.composer, php.cache)
    assert unloaded.command("x.php", coverage=True) == (
        executable,
        "-d",
        "display_errors=stderr",
        "-d",
        "zend_extension=xdebug",
        "x.php",
    )
    assert php.composer_command("install") == php.command(str(php.composer), "install")


def test_the_environment_isolates_composer_and_switches_xdebug(php: Php, tmp_path: Path) -> None:
    home = tmp_path / "home"
    env = php.environ(home, {"EXTRA": "1"})
    assert home.is_dir()
    assert env["PATH"].split(":")[0] == str(php.executable.parent)
    assert (env["HOME"], env["COMPOSER_HOME"]) == (str(home), str(home / "composer"))
    assert env["COMPOSER_CACHE_DIR"] == str(php.cache)
    assert env["COMPOSER_NO_INTERACTION"] == env["COMPOSER_NO_AUDIT"] == "1"
    assert env["COMPOSER_DISABLE_NETWORK"] == "1"
    assert (env["COLUMNS"], env["XDEBUG_MODE"], env["EXTRA"]) == ("4000", "off", "1")
    online = php.environ(home, coverage=True, online=True)
    assert "COMPOSER_DISABLE_NETWORK" not in online
    assert online["XDEBUG_MODE"] == "coverage"
    command = php.run_in(("php",), tmp_path, home, timeout=5.0, online=True)
    assert (command.cwd, command.timeout, command.reap) == (tmp_path, 5.0, True)
    assert "COMPOSER_DISABLE_NETWORK" not in (command.env or {})


def test_the_system_php_comes_from_the_system_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "second").mkdir()
    (tmp_path / "second" / "php").write_text("")
    directories = (str(tmp_path / "first"), str(tmp_path / "second"))
    monkeypatch.setattr(runtime, "SYSTEM_PATH", directories)
    assert system_php() == tmp_path / "second" / "php"
    monkeypatch.setattr(runtime, "SYSTEM_PATH", directories[:1])
    assert failure(system_php) == f"php: PHP is not installed in {directories[0]}"


def test_a_php_with_xdebug_loaded_by_the_system_is_accepted(fake_php: FakePhp) -> None:
    php = fake_php.php
    found = probed(php.executable, php.composer, php.cache, REQUIRED)
    assert found == Php(php.executable, "8.4.14", True, php.composer, php.cache)
    assert fake_php.scripts() == ["probe"]
    assert fake_php.calls()[0][0] == str(php.executable.parent)


def test_xdebug_that_the_system_does_not_load_is_loaded_by_the_gate(tmp_path: Path) -> None:
    fake = FakePhp.create(tmp_path / "fake", tmp_path / "cache", loaded=False)
    php = fake.php
    found = probed(php.executable, php.composer, php.cache, REQUIRED)
    assert not found.xdebug_loaded
    assert fake.scripts() == ["probe", "probe"]
    assert "zend_extension=xdebug" in fake.calls()[1]


@pytest.mark.parametrize(
    ("handler", "problem"),
    [
        (probe("8.3.9"), "is PHP 8.3.9; the profile requires 8.4; "),
        (probe(xdebug=None, loaded=False), "Xdebug is not available to "),
        (probe(xdebug="3.4.7"), "has Xdebug 3.4.7; the profile requires 3.5; "),
    ],
    ids=["php", "no Xdebug", "Xdebug"],
)
def test_php_and_xdebug_must_be_the_required_minor_versions(
    fake_php: FakePhp, handler: str, problem: str
) -> None:
    fake_php.handle("probe", handler)
    php = fake_php.php
    message = failure(lambda: probed(php.executable, php.composer, php.cache, REQUIRED))
    assert problem in message
    assert message.endswith("install PHP 8.4 with Xdebug 3.5 (decision 0002)")


def test_pcov_does_not_replace_xdebug(fake_php: FakePhp) -> None:
    fake_php.handle("probe", probe(xdebug=None, loaded=False))
    php = fake_php.php
    message = failure(lambda: probed(php.executable, php.composer, php.cache, REQUIRED))
    assert "branch coverage needs it, which PCOV and phpdbg cannot replace" in message


@pytest.mark.parametrize(
    ("handler", "problem"),
    [
        ("echo not json\n", "probe"),
        ("printf '[1]'\n", "answered the probe with [1]"),
        ("""printf '{"php": 8}'\n""", "answered the probe with {'php': 8}"),
        ("""printf '{"php": "8.4.1", "xdebug": 3}'\n""", "answered the probe with"),
        ("exit 3\n", "exit status 3"),
    ],
    ids=["text", "list", "version", "xdebug", "status"],
)
def test_a_probe_that_php_answers_badly_fails(
    fake_php: FakePhp, handler: str, problem: str
) -> None:
    fake_php.handle("probe", handler)
    php = fake_php.php
    assert problem in failure(lambda: probed(php.executable, php.composer, php.cache, REQUIRED))


def test_a_missing_composer_names_the_bootstrap_command() -> None:
    def missing() -> Path:
        native.fail("composer", "composer 2.10.3 is not installed")

    message = failure(lambda: located(missing))
    assert message == (
        "composer: composer 2.10.3 is not installed; "
        "run `./quality bootstrap` to install the pinned Composer"
    )


def test_the_runtime_probes_once(fake_php: FakePhp) -> None:
    php = fake_php.php
    found = Runtime(lambda: php.executable, lambda: php.composer, php.cache, REQUIRED)
    assert found.php() is found.php()
    assert found.php().composer == php.composer
    assert fake_php.scripts() == ["probe"]


def test_requirements_say_what_to_install() -> None:
    assert Requirements("8.4", "3.5").hint == "install PHP 8.4 with Xdebug 3.5 (decision 0002)"
