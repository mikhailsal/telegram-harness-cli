from pathlib import Path

import pytest
import yaml

from quality.adapters.dart.environment import SDK_HINT
from quality.adapters.dart.helper import Helper
from quality.adapters.dart.lockfile import Lock
from quality.adapters.dart.provision import DartSetup, copy_manifests
from quality.adapters.native import ToolFailedError
from quality.testkit.dartfakes import (
    PINS,
    FakeDart,
    dart_component,
    pubspec,
    pubspec_lock,
    write_component,
    write_helper,
)
from quality.toolchain.install import MISSING

APP = dart_component()
# The fake `pub get` lists the files it was given.
LISTING = 'find . -type f | sort | sed "s|^./||" > "$(dirname "$0")/listing"\n'


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_only_the_manifests_pub_reads_inside_the_repository_are_copied(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write(root / "pubspec.yaml", yaml.safe_dump({"name": "w", "workspace": ["packages/a"]}))
    write(root / "pubspec.lock")
    write(root / "README.md")
    write(root / "packages" / "a" / "pubspec.yaml", "name: a\nresolution: workspace\n")
    write(root / "packages" / "a" / "lib" / "a.dart")
    write(root / "local" / "pubspec.yaml", "name: local\n")
    write(root / "local" / "lib" / "local.dart")
    write(tmp_path / "outside" / "pubspec.yaml", "name: outside\n")
    lock = Lock(
        root,
        {
            "local": {"source": "path", "description": {"path": "local", "relative": True}},
            "outside": {"source": "path", "description": {"path": "../outside", "relative": True}},
            "missing": {"source": "path", "description": {"path": "missing", "relative": True}},
        },
    )
    target = tmp_path / "copy"
    assert copy_manifests(lock, root, target) == target
    copied = sorted(path.relative_to(target).as_posix() for path in target.rglob("*.*"))
    assert copied == [
        "local/pubspec.yaml",
        "packages/a/pubspec.yaml",
        "pubspec.lock",
        "pubspec.yaml",
    ]
    nested = Lock(root / "packages" / "a", {})
    assert copy_manifests(nested, root, tmp_path / "member") == tmp_path / "member/packages/a"


def setup(tmp_path: Path, fake_dart: FakeDart) -> tuple[DartSetup, list[str]]:
    calls: list[str] = []

    def locate() -> Path:
        calls.append("locate")
        return fake_dart.sdk.executable

    def install() -> Path:
        calls.append("install")
        return fake_dart.sdk.executable

    helper = Helper(write_helper(tmp_path / "helper"), tmp_path / "helper-cache")
    dart = DartSetup(locate, install, fake_dart.sdk.cache, PINS, helper)
    return dart, calls


def test_bootstrap_installs_resolves_online_and_compiles_the_helper(
    tmp_path: Path, fake_dart: FakeDart, snapshot: Path
) -> None:
    write_component(snapshot / "app")
    write(snapshot / "app" / "lib" / "a.dart")
    fake_dart.handle("pub", LISTING)
    dart, calls = setup(tmp_path, fake_dart)
    assert dart.provision(APP, snapshot) == []
    assert dart.provision(APP, snapshot) == []
    assert calls == ["install", "install"]
    gets = [call for call in fake_dart.calls() if call[2] == "pub"]
    assert [call[2:] for call in gets] == [["pub", "get", "--enforce-lockfile"]] * 2
    component, helper = (Path(call[0]) for call in gets)
    assert component.name == "app"
    assert component.parent.name == "packages"
    assert helper.name == "helper"
    listing = (fake_dart.root / "handlers" / "listing").read_text().splitlines()
    assert "lib/cli.dart" in listing
    assert fake_dart.commands() == ["pub", "pub", "compile"]


def test_doctor_resolves_offline_once_per_lock_directory(
    tmp_path: Path, fake_dart: FakeDart, snapshot: Path
) -> None:
    write_component(snapshot / "app")
    fake_dart.handle("pub", LISTING)
    dart, calls = setup(tmp_path, fake_dart)
    dart.probe(APP, snapshot)
    listing = (fake_dart.root / "handlers" / "listing").read_text().splitlines()
    dart.probe(APP, snapshot)
    assert calls == ["locate", "locate"]
    assert fake_dart.commands() == ["pub", "pub", "compile"]
    assert fake_dart.calls()[0][-1] == "--offline"
    assert listing == ["bin/main.dart", "lib/cli.dart", "pubspec.lock", "pubspec.yaml"]


def test_the_component_copy_holds_only_its_manifests(
    tmp_path: Path, fake_dart: FakeDart, snapshot: Path
) -> None:
    write_component(snapshot / "app")
    write(snapshot / "app" / "lib" / "a.dart")
    fake_dart.handle("pub", LISTING + "exit 3\n")
    dart, _ = setup(tmp_path, fake_dart)
    with pytest.raises(ToolFailedError, match="dart pub get: app: exit status 3"):
        dart.probe(APP, snapshot)
    listing = (fake_dart.root / "handlers" / "listing").read_text().splitlines()
    assert listing == ["pubspec.lock", "pubspec.yaml"]


def test_a_rejected_configuration_needs_no_sdk(
    tmp_path: Path, fake_dart: FakeDart, snapshot: Path
) -> None:
    write(snapshot / "app" / "pubspec.yaml", pubspec("calc"))
    write(snapshot / "app" / "pubspec.lock", pubspec_lock({}))
    dart, calls = setup(tmp_path, fake_dart)
    assert dart.probe(APP, snapshot) != []
    assert calls == []
    assert fake_dart.calls() == []


def test_a_missing_sdk_names_the_bootstrap_command(
    tmp_path: Path, fake_dart: FakeDart, snapshot: Path
) -> None:
    write_component(snapshot / "app")

    def absent() -> Path:
        raise ToolFailedError(MISSING.diagnostic("dart 3.13.6 is not installed"))

    helper = Helper(write_helper(tmp_path / "helper"), tmp_path / "helper-cache")
    dart = DartSetup(absent, absent, fake_dart.sdk.cache, PINS, helper)
    with pytest.raises(ToolFailedError, match=SDK_HINT.replace(".", r"\.")):
        dart.probe(APP, snapshot)
