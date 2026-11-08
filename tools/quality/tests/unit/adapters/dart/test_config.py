from pathlib import Path

import pytest
import yaml

from quality.adapters.dart.config import CONFIG, check_config, dart_files
from quality.testkit.dartfakes import PINS, dart_component, pubspec, write_component

APP = dart_component()
VM_ONLY = "the Dart profile supports the Dart VM only"


def findings(snapshot: Path) -> list[tuple[str, str]]:
    _, report = check_config(APP, snapshot, PINS)
    assert {diagnostic.rule for diagnostic in report.found} <= {CONFIG.id}
    return [
        (diagnostic.location.path if diagnostic.location else "", diagnostic.message)
        for diagnostic in report.found
    ]


def test_dart_files_are_selected_by_suffix() -> None:
    assert dart_files(("a.dart", "b.py", "c.dart.txt", "lib/d.dart")) == ("a.dart", "lib/d.dart")


def test_a_valid_component_has_a_name_and_a_lock(snapshot: Path) -> None:
    directory = write_component(snapshot / "app")
    settings, report = check_config(APP, snapshot, PINS)
    assert report.found == []
    assert settings is not None
    assert (settings.name, settings.lock.directory) == ("calc", directory)
    assert settings.lock.packages["test"]["version"] == PINS["test"]


def test_a_component_without_a_usable_pubspec_stops_at_once(snapshot: Path) -> None:
    (snapshot / "app").mkdir()
    ((path, message),) = findings(snapshot)
    assert path == "app/pubspec.yaml"
    assert message.startswith("a pubspec.yaml holding a YAML mapping is required (")


@pytest.mark.parametrize("name", [None, 3, "Calc", "my-app", "1app", ""])
def test_the_package_name_must_be_a_dart_identifier(snapshot: Path, name: object) -> None:
    directory = write_component(snapshot / "app")
    document = yaml.safe_load((directory / "pubspec.yaml").read_text())
    document["name"] = name
    (directory / "pubspec.yaml").write_text(yaml.safe_dump(document))
    assert findings(snapshot) == [
        ("app/pubspec.yaml", "`name` must be lowercase letters, digits, and underscores")
    ]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"flutter": {"uses-material-design": True}}, "a `flutter` section makes this"),
        ({"environment": {"sdk": "^3.13.0", "flutter": ">=3.0.0"}}, "an `environment.flutter`"),
        ({"dependencies": {"flutter": {"sdk": "flutter"}}}, "flutter comes from the flutter SDK"),
        (
            {"dev_dependencies": {**PINS, "flutter_test": {"sdk": "flutter"}}},
            "flutter_test comes from the flutter SDK",
        ),
        (
            {"dependency_overrides": {"build_web_compilers": "4.0.0"}},
            "build_web_compilers compiles for the web",
        ),
    ],
)
def test_flutter_and_web_packages_are_rejected(
    snapshot: Path, fields: dict[str, object], message: str
) -> None:
    directory = write_component(snapshot / "app")
    (directory / "pubspec.yaml").write_text(pubspec("calc", **fields))
    ((path, found),) = findings(snapshot)
    assert path == "app/pubspec.yaml"
    assert found.startswith(message)
    assert found.endswith(f"; {VM_ONLY}")


def test_ordinary_dependencies_are_accepted(snapshot: Path) -> None:
    directory = write_component(snapshot / "app")
    fields = {"dependencies": {"path": "^1.9.0", "meta": {"hosted": "https://pub.dev"}}}
    (directory / "pubspec.yaml").write_text(pubspec("calc", **fields))
    assert findings(snapshot) == []


def test_options_test_settings_and_the_lock_are_all_checked(snapshot: Path) -> None:
    directory = write_component(snapshot / "app")
    (directory / "analysis_options.yaml").unlink()
    (directory / "dart_test.yaml").write_text("tags: {}\n")
    (directory / "pubspec.lock").unlink()
    settings, report = check_config(APP, snapshot, PINS)
    assert settings is None
    assert [diagnostic.location.path for diagnostic in report.found if diagnostic.location] == [
        "app/analysis_options.yaml",
        "app/dart_test.yaml",
        "app/pubspec.lock",
    ]
