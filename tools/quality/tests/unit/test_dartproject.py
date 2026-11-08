from pathlib import Path

import pytest
import yaml

from quality.testkit import dartproject, scratch
from quality.testkit.dartproject import (
    dart_project,
    lock,
    package_files,
    pinned_dart,
    pinned_packages,
    pub_cache,
    pubspec,
    workspace,
)
from quality.testkit.nodefakes import script
from quality.testkit.scratch import create_project, dart

PINS = {"test": "1.33.0", "coverage": "1.16.0", "lints": "6.1.0"}
# The fake pub logs its directory, arguments, and cache, then writes a lockfile
# and the tool directory pub would leave behind.
PUB = """\
echo "$PWD $* $PUB_CACHE" >> "{log}"
echo "packages: {{}}" > pubspec.lock
mkdir -p .dart_tool
"""


@pytest.fixture
def fake_dart(tmp_path: Path) -> Path:
    return script(tmp_path / "sdk" / "bin" / "dart", PUB.format(log=tmp_path / "calls.log"))


def test_the_pinned_packages_come_from_the_manifest() -> None:
    assert pinned_packages() == PINS


def test_the_pinned_sdk_and_pub_cache_are_found_beside_the_toolchains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "toolchains"
    executable = script(root / "dart-3.13.6" / "bin" / "dart", "exit 0\n")
    monkeypatch.setattr(dartproject, "cache_root", lambda: root)
    assert pinned_dart() == executable
    assert pub_cache() == tmp_path / "pub"


def test_manifests_pin_the_profile_packages() -> None:
    document = yaml.safe_load(pubspec("calc", resolution="workspace"))
    assert document == {
        "name": "calc",
        "publish_to": "none",
        "environment": {"sdk": "^3.13.0"},
        "dev_dependencies": PINS,
        "resolution": "workspace",
    }
    assert "dev_dependencies" not in yaml.safe_load(pubspec("bare", pinned=False))
    root = yaml.safe_load(workspace(["packages/a", "packages/b"]))
    assert root["workspace"] == ["packages/a", "packages/b"]


def test_lockfiles_are_written_offline_from_the_cache(fake_dart: Path, tmp_path: Path) -> None:
    directory = tmp_path / "calc"
    directory.mkdir()
    assert lock(directory, fake_dart) == directory / "pubspec.lock"
    assert (tmp_path / "calls.log").read_text() == f"{directory} pub get --offline {pub_cache()}\n"
    assert not (directory / ".dart_tool").exists()


def test_a_lockfile_pub_cannot_write_fails_the_arrangement(tmp_path: Path) -> None:
    failing = script(tmp_path / "dart", "echo 'no cached package' >&2\nexit 1\n")
    with pytest.raises(AssertionError, match=r"pub could not lock .*: no cached package"):
        lock(tmp_path, failing)
    silent = script(tmp_path / "quiet" / "dart", "echo 'offline' \nexit 69\n")
    with pytest.raises(AssertionError, match=r"pub could not lock .*: offline"):
        lock(tmp_path, silent)


def test_components_are_written_complete(fake_dart: Path, tmp_path: Path) -> None:
    calc = dart_project(tmp_path / "calc", dart=fake_dart)
    assert sorted(path.relative_to(calc).as_posix() for path in calc.rglob("*.*")) == [
        "analysis_options.yaml",
        "lib/core.dart",
        "pubspec.lock",
        "pubspec.yaml",
        "test/core_test.dart",
    ]
    assert "import 'package:calc/core.dart';" in (calc / "test/core_test.dart").read_text()
    other = dart_project(tmp_path / "other", {"lib/a.dart": ""}, name="other", dart=fake_dart)
    assert (other / "lib/a.dart").is_file()
    assert not (other / "lib/core.dart").exists()
    assert yaml.safe_load(package_files("other")["pubspec.yaml"])["name"] == "other"


def test_scratch_projects_hold_dart_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[tuple[Path, str]] = []

    def record(directory: Path, *, name: str) -> Path:
        written.append((directory, name))
        dartproject.write(directory, {"pubspec.yaml": pubspec(name, pinned=False)})
        return directory

    monkeypatch.setattr(scratch, "dart_project", record)
    project = create_project(tmp_path / "project", dart())
    assert written == [(project.path / "calc", "calc")]
    policy = (project.path / "quality.toml").read_text()
    assert 'profile = "dart"' in policy
    assert 'source = ["lib"]' in policy
    assert 'unit = ["test"]' in policy
    assert ".dart_tool/" in (project.path / ".gitignore").read_text()
    assert project.repo.git("status", "--porcelain") == ""
