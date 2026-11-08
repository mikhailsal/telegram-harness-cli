import json
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.php.helper import Helper
from quality.adapters.php.lockfile import Lock
from quality.adapters.php.provision import PhpSetup, copy_manifests
from quality.testkit.phpfakes import (
    PINS,
    REQUIRED,
    FakePhp,
    php_component,
    write_component,
    write_helper,
)

APP = php_component()
# The fake `composer install` lists the files of the directory it installs in, by name.
LISTING = """\
find . -type f | sort | sed "s|^./||" > "$(dirname "$0")/listing-$(basename "$PWD")"
mkdir -p vendor
echo '<?php' > vendor/autoload.php
echo "${COMPOSER_DISABLE_NETWORK:-online}" >> "$(dirname "$0")/network"
"""


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_only_the_manifests_composer_reads_inside_the_repository_are_copied(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    write(root / "composer.json", "{}")
    write(root / "composer.lock", "{}")
    write(root / "README.md")
    write(root / "packages" / "a" / "composer.json", "{}")
    write(root / "packages" / "a" / "src" / "A.php")
    write(tmp_path / "outside" / "composer.json", "{}")

    def path(url: str) -> dict[str, object]:
        return {"dist": {"type": "path", "url": url}}

    lock = Lock(
        root,
        {
            "w/a": path("packages/a"),
            "w/outside": path("../outside"),
            "w/missing": path("missing"),
            "w/zip": {"dist": {"type": "zip", "url": "https://e.com/a.zip"}},
        },
    )
    target = tmp_path / "copy"
    assert copy_manifests(lock, root, target) == target
    copied = sorted(path.relative_to(target).as_posix() for path in target.rglob("*.*"))
    assert copied == ["composer.json", "composer.lock", "packages/a/composer.json"]
    nested = Lock(root / "packages" / "a", {})
    assert copy_manifests(nested, root, tmp_path / "member") == tmp_path / "member/packages/a"


def setup(tmp_path: Path, fake_php: FakePhp) -> tuple[PhpSetup, list[str]]:
    calls: list[str] = []
    php = fake_php.php

    def locate() -> Path:
        calls.append("locate")
        return php.composer

    def install() -> Path:
        calls.append("install")
        return php.composer

    helper = Helper(write_helper(tmp_path / "helper"), tmp_path / "helper-cache")
    found = PhpSetup(lambda: php.executable, locate, install, php.cache, REQUIRED, PINS, helper)
    return found, calls


def listing(fake_php: FakePhp, name: str) -> list[str]:
    return (fake_php.root / "handlers" / f"listing-{name}").read_text().splitlines()


def test_bootstrap_installs_online_and_installs_the_helper(
    tmp_path: Path, fake_php: FakePhp, snapshot: Path
) -> None:
    write_component(snapshot / "app")
    write(snapshot / "app" / "src" / "A.php")
    fake_php.handle("composer", LISTING)
    php, calls = setup(tmp_path, fake_php)
    assert php.provision(APP, snapshot) == []
    assert php.provision(APP, snapshot) == []
    assert calls == ["install", "install"]
    assert fake_php.scripts() == ["probe", "composer", "composer", "probe"]
    component, helper = (Path(call[0]) for call in fake_php.calls() if "install" in call)
    assert (component.name, component.parent.name) == ("app", "packages")
    assert helper.name == "helper"
    assert listing(fake_php, "app") == ["composer.json", "composer.lock"]
    assert "src/Cli.php" in listing(fake_php, "helper")
    network = (fake_php.root / "handlers" / "network").read_text().split()
    assert network == ["online", "online"]


def test_doctor_installs_offline_once_per_lock_directory(
    tmp_path: Path, fake_php: FakePhp, snapshot: Path
) -> None:
    write_component(snapshot / "app")
    fake_php.handle("composer", LISTING)
    php, calls = setup(tmp_path, fake_php)
    assert php.probe(APP, snapshot) == []
    assert php.probe(APP, snapshot) == []
    assert calls == ["locate", "locate"]
    assert fake_php.scripts() == ["probe", "composer", "composer", "probe"]
    install = next(call for call in fake_php.calls() if "install" in call)
    assert "--no-dev" not in install
    network = (fake_php.root / "handlers" / "network").read_text().split()
    assert network == ["1", "1"]


def test_a_rejected_configuration_installs_nothing(
    tmp_path: Path, fake_php: FakePhp, snapshot: Path
) -> None:
    directory = write_component(snapshot / "app")
    document = json.loads((directory / "composer.lock").read_text())
    document["packages-dev"] = []
    (directory / "composer.lock").write_text(json.dumps(document))
    php, calls = setup(tmp_path, fake_php)
    found = php.provision(APP, snapshot)
    assert [item.location.path if item.location else "" for item in found] == [
        "app/composer.lock"
    ] * len(PINS)
    assert calls == []
    assert fake_php.calls() == []


def test_a_failed_installation_names_the_component(
    tmp_path: Path, fake_php: FakePhp, snapshot: Path
) -> None:
    write_component(snapshot / "app")
    fake_php.handle("composer", "echo 'Could not download' >&2\nexit 1\n")
    php, _ = setup(tmp_path, fake_php)
    with pytest.raises(ToolFailedError, match="composer install: app: Could not download"):
        php.provision(APP, snapshot)
