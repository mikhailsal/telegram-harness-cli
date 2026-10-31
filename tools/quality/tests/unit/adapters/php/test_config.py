import json
from pathlib import Path

import pytest

from quality.adapters.php.config import CONFIG, Prefix, check_config, php_files
from quality.testkit.phpfakes import PINS, composer_json, php_component, write_component

APP = php_component()


def findings(snapshot: Path) -> list[tuple[str, str]]:
    _, report = check_config(APP, snapshot, PINS)
    assert {diagnostic.rule for diagnostic in report.found} <= {CONFIG.id}
    assert all(diagnostic.component == "app" for diagnostic in report.found)
    return [
        (diagnostic.location.path if diagnostic.location else "", diagnostic.message)
        for diagnostic in report.found
    ]


def manifest(snapshot: Path, **fields: object) -> None:
    write_component(snapshot / "app")
    (snapshot / "app" / "composer.json").write_text(composer_json(**fields))


def test_php_files_are_selected_by_suffix() -> None:
    assert php_files(("a.php", "b.py", "c.php.txt", "src/d.php")) == ("a.php", "src/d.php")


def test_a_valid_component_has_its_prefixes_and_a_lock(snapshot: Path) -> None:
    directory = write_component(snapshot / "app")
    settings, report = check_config(APP, snapshot, PINS)
    assert report.found == []
    assert settings is not None
    assert settings.autoload == (Prefix("App\\", ("src/",)), Prefix("App\\Tests\\", ("tests/",)))
    assert settings.lock.directory == directory


def test_a_component_without_a_usable_manifest_stops_at_once(snapshot: Path) -> None:
    (snapshot / "app").mkdir()
    ((path, message),) = findings(snapshot)
    assert path == "app/composer.json"
    assert message.startswith("a composer.json holding a JSON object is required (")


def test_a_missing_lock_stops_the_configuration(snapshot: Path) -> None:
    write_component(snapshot / "app")
    (snapshot / "app" / "composer.lock").unlink()
    assert check_config(APP, snapshot, PINS)[0] is None
    assert findings(snapshot) == [
        (
            "app/composer.lock",
            "no composer.lock beside composer.json; run `composer update` and commit it",
        )
    ]


@pytest.mark.parametrize("vendor", ["lib", "../vendor", 3])
def test_the_vendor_directory_must_be_vendor(snapshot: Path, vendor: object) -> None:
    manifest(snapshot, config={"vendor-dir": vendor})
    assert findings(snapshot) == [
        (
            "app/composer.json",
            f"`config.vendor-dir` is {vendor!r}; the profile installs into `vendor`",
        )
    ]


def test_the_default_vendor_directory_may_be_named(snapshot: Path) -> None:
    manifest(snapshot, config={"vendor-dir": "vendor", "sort-packages": True})
    assert findings(snapshot) == []


def test_psr4_prefixes_name_directories_inside_the_repository(snapshot: Path) -> None:
    manifest(
        snapshot,
        autoload={"psr-4": {"A\\": ["src/", "lib/"], "B\\": "../shared/", "C\\": 3, "D\\": [1]}},
        **{"autoload-dev": {"psr-4": {"E\\": "../../elsewhere/", "F\\": "/abs"}}},
    )
    settings, _ = check_config(APP, snapshot, PINS)
    assert settings is not None
    assert settings.autoload == (Prefix("A\\", ("src/", "lib/")), Prefix("B\\", ("../shared/",)))
    assert findings(snapshot) == [
        ("app/composer.json", "`autoload.psr-4.C\\` must name directories"),
        ("app/composer.json", "`autoload.psr-4.D\\` must name directories"),
        (
            "app/composer.json",
            "`autoload-dev.psr-4.E\\` names '../../elsewhere/', outside the repository",
        ),
        ("app/composer.json", "`autoload-dev.psr-4.F\\` names '/abs', outside the repository"),
    ]


def test_a_component_without_psr4_prefixes_has_none(snapshot: Path) -> None:
    write_component(snapshot / "app")
    document = json.loads((snapshot / "app" / "composer.json").read_text())
    del document["autoload"], document["autoload-dev"]
    (snapshot / "app" / "composer.json").write_text(json.dumps(document))
    settings, report = check_config(APP, snapshot, PINS)
    assert report.found == []
    assert settings is not None
    assert settings.autoload == ()
