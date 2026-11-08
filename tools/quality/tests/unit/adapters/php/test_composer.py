from collections.abc import Callable
from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.php.composer import (
    DOWNLOAD_TIMEOUT,
    INSTALL_TIMEOUT,
    Environments,
    install,
    install_command,
    validate,
)
from quality.adapters.php.runtime import Php, Runtime
from quality.testkit.phpfakes import REQUIRED, FakePhp

LOCK_ERRORS = """\
cat >&2 <<'OUT'
./composer.json is valid but your composer.lock has some errors
# Lock file errors
- The lock file is not up to date with the latest changes in composer.json, it is \
recommended that you run `composer update` or `composer update <package name>`.
- Required package "psr/log" is not present in the lock file.
# General errors
- name : Does not match the regex pattern ^[a-z0-9]$
This usually happens when composer files are incorrectly merged.
OUT
exit 2
"""
UNLOADABLE = """\
cat >&2 <<'OUT'

In Factory.php line 317:

  "./composer.json" does not match the expected JSON schema:
   - name : Does not match the regex pattern ^[a-z0-9]$

validate [--no-check-all] [--check-lock] [--] [<file>]

OUT
exit 1
"""


def failure(call: Callable[[], object]) -> str:
    with pytest.raises(native.ToolFailedError) as raised:
        call()
    return raised.value.diagnostic.message


def test_a_valid_manifest_has_no_problems(fake_php: FakePhp, php: Php, tmp_path: Path) -> None:
    fake_php.handle("composer", "echo './composer.json is valid'\n")
    assert validate(php, tmp_path, tmp_path / "home") == []
    (call,) = fake_php.calls()
    assert call[0] == str(tmp_path)
    assert call[-5:] == [
        str(php.composer),
        "validate",
        "--no-check-publish",
        "--no-check-all",
        "--check-lock",
    ]


def test_validation_errors_are_reported_by_file(
    fake_php: FakePhp, php: Php, tmp_path: Path
) -> None:
    fake_php.handle("composer", LOCK_ERRORS)
    assert validate(php, tmp_path, tmp_path / "home") == [
        (
            "composer.lock",
            (
                "The lock file is not up to date with the latest changes in composer.json, it "
                "is recommended that you run `composer update` or `composer update <package "
                "name>`."
            ),
        ),
        ("composer.lock", 'Required package "psr/log" is not present in the lock file.'),
        ("composer.json", "name : Does not match the regex pattern ^[a-z0-9]$"),
    ]


def test_a_manifest_composer_cannot_load_is_reported_with_its_exception(
    fake_php: FakePhp, php: Php, tmp_path: Path
) -> None:
    fake_php.handle("composer", UNLOADABLE)
    assert validate(php, tmp_path, tmp_path / "home") == [
        (
            "composer.json",
            (
                '"./composer.json" does not match the expected JSON schema: '
                "- name : Does not match the regex pattern ^[a-z0-9]$"
            ),
        )
    ]
    fake_php.handle("composer", "exit 1\n")
    assert validate(php, tmp_path, tmp_path / "home") == [
        ("composer.json", "composer.json cannot be loaded")
    ]


def test_unexplained_validation_failures_fail(fake_php: FakePhp, php: Php, tmp_path: Path) -> None:
    fake_php.handle("composer", "echo 'something odd' >&2\nexit 2\n")
    assert failure(lambda: validate(php, tmp_path, tmp_path / "home")) == (
        "composer validate: exit status 2 without errors: something odd"
    )
    fake_php.handle("composer", "exit 3\n")
    assert "exit status 3" in failure(lambda: validate(php, tmp_path, tmp_path / "home"))


def test_installation_skips_scripts_and_plugins(
    fake_php: FakePhp, php: Php, tmp_path: Path
) -> None:
    command = install_command(php, tmp_path, tmp_path / "home")
    assert command.argv[-4:] == ("install", "--no-progress", "--no-scripts", "--no-plugins")
    assert (command.timeout, command.cwd) == (INSTALL_TIMEOUT, tmp_path)
    assert (command.env or {})["COMPOSER_DISABLE_NETWORK"] == "1"
    online = install_command(php, tmp_path, tmp_path / "home", online=True, development=False)
    assert online.argv[-1] == "--no-dev"
    assert online.timeout == DOWNLOAD_TIMEOUT
    assert "COMPOSER_DISABLE_NETWORK" not in (online.env or {})
    install(command, "app")
    assert fake_php.scripts() == ["composer"]


def test_a_failed_installation_names_the_bootstrap_command(
    fake_php: FakePhp, php: Php, tmp_path: Path
) -> None:
    command = install_command(php, tmp_path, tmp_path / "home")
    hint = "run `./quality bootstrap` to download the locked packages"
    fake_php.handle("composer", "echo 'The package is not in the cache' >&2\nexit 1\n")
    assert failure(lambda: install(command, "app")) == (
        f"composer install: app: The package is not in the cache; {hint}"
    )
    fake_php.handle("composer", "echo 'only stdout'\nexit 1\n")
    assert failure(lambda: install(command, "app")) == f"composer install: app: only stdout; {hint}"
    fake_php.handle("composer", "exit 5\n")
    assert (
        failure(lambda: install(command, "app")) == f"composer install: app: exit status 5; {hint}"
    )
    missing = install_command(php, tmp_path / "missing", tmp_path / "home")
    assert failure(lambda: install(missing, "app")).startswith("composer install: app: ")


def ignore(file: str, message: str) -> None:
    del file, message


def environments(fake_php: FakePhp) -> Environments:
    php = fake_php.php
    return Environments(Runtime(lambda: php.executable, lambda: php.composer, php.cache, REQUIRED))


def test_a_component_is_validated_and_installed_once_per_work_area(
    fake_php: FakePhp, tmp_path: Path
) -> None:
    found = environments(fake_php)
    work = tmp_path / "work"
    php = found.install(tmp_path, work, label="app", add=ignore)
    assert php == found.install(tmp_path, work, label="app", add=ignore)
    assert fake_php.scripts() == ["probe", "composer", "composer"]
    assert [call[-1] for call in fake_php.calls()[1:]] == ["--check-lock", "--no-plugins"]
    assert (work / "php-home" / "app").is_dir()
    found.install(tmp_path, tmp_path / "other", label="app", add=ignore)
    assert fake_php.scripts()[3:] == ["composer", "composer"]


def test_validation_problems_stop_the_installation(fake_php: FakePhp, tmp_path: Path) -> None:
    fake_php.handle("composer", LOCK_ERRORS)
    found = environments(fake_php)
    problems: list[tuple[str, str]] = []
    found.install(
        tmp_path,
        tmp_path / "work",
        label="app",
        add=lambda file, message: problems.append((file, message)),
    )
    assert [file for file, _ in problems] == ["composer.lock", "composer.lock", "composer.json"]
    assert all(message.startswith("composer validate: ") for _, message in problems)
    assert fake_php.scripts() == ["probe", "composer"]
    found.install(tmp_path, tmp_path / "work", label="app", add=ignore)
    assert fake_php.scripts() == ["probe", "composer", "composer"]
