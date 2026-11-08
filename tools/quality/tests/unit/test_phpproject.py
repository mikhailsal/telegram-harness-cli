import json
from pathlib import Path

import pytest

from quality.adapters.php.helper import HELPER
from quality.testkit import phpproject, scratch
from quality.testkit.phpproject import lockfile, manifest, php_project, requirements
from quality.testkit.scratch import create_project, php


def test_the_component_requires_what_the_helper_requires() -> None:
    helper = json.loads((HELPER / "composer.json").read_text())
    assert requirements() == {"require": helper["require"], "require-dev": helper["require-dev"]}
    assert "phpunit/phpunit" in helper["require-dev"]


def test_manifests_map_the_app_prefixes_and_take_overrides() -> None:
    document = json.loads(manifest())
    assert document == {
        "type": "project",
        **requirements(),
        "autoload": {"psr-4": {"App\\": "src/"}},
        "autoload-dev": {"psr-4": {"App\\Tests\\": "tests/"}},
    }
    changed = json.loads(manifest(autoload={"psr-4": {"Shop\\": "lib/"}}, name="acme/shop"))
    assert (changed["autoload"], changed["name"]) == ({"psr-4": {"Shop\\": "lib/"}}, "acme/shop")


def test_the_lockfile_is_the_helpers() -> None:
    assert lockfile() == (HELPER / "composer.lock").read_text()


def test_components_are_written_complete(tmp_path: Path) -> None:
    shop = php_project(tmp_path / "shop")
    assert sorted(path.relative_to(shop).as_posix() for path in shop.rglob("*.*")) == [
        "composer.json",
        "composer.lock",
        "src/Sign.php",
        "tests/SignTest.php",
    ]
    assert "use App\\Sign;" in (shop / "tests/SignTest.php").read_text()
    other = php_project(tmp_path / "other", {"src/Other/A.php": "<?php\n"})
    assert (other / "src/Other/A.php").is_file()
    assert not (other / "src/Sign.php").exists()
    assert (other / "composer.lock").read_text() == lockfile()


def test_scratch_projects_hold_php_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[Path] = []

    def record(directory: Path) -> Path:
        written.append(directory)
        phpproject.write(directory, {"composer.json": "{}\n"})
        return directory

    monkeypatch.setattr(scratch, "php_project", record)
    project = create_project(tmp_path / "project", php())
    assert written == [project.path / "shop"]
    policy = (project.path / "quality.toml").read_text()
    assert 'profile = "php"' in policy
    assert 'source = ["src"]' in policy
    assert 'unit = ["tests"]' in policy
    assert "vendor/" in (project.path / ".gitignore").read_text()
    assert project.repo.git("status", "--porcelain") == ""
