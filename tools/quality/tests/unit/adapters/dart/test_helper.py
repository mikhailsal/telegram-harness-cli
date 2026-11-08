import json
from pathlib import Path

import pytest

from quality.adapters.dart.helper import HELPER, Helper, digest, helper_files
from quality.adapters.native import ToolFailedError
from quality.testkit.dartfakes import FakeDart, write_helper

ANSWER = 'printf \'{"protocol": 1, "files": []}\'\n'


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return write_helper(tmp_path / "helper")


def test_the_helper_lives_in_the_core() -> None:
    assert (HELPER / "pubspec.yaml").is_file()
    assert (HELPER / "bin" / "main.dart").is_file()


def test_the_snapshot_is_built_from_the_manifests_and_the_program_files(
    tmp_path: Path, source: Path
) -> None:
    (source / "lib" / "src").mkdir()
    (source / "lib" / "src" / "a.dart").write_text("")
    (source / "test").mkdir()
    (source / "test" / "a_test.dart").write_text("")
    assert helper_files(source) == [
        "pubspec.yaml",
        "pubspec.lock",
        "bin/main.dart",
        "lib/cli.dart",
        "lib/src/a.dart",
    ]
    bare = tmp_path / "bare"
    (bare / "bin").mkdir(parents=True)
    (bare / "bin" / "main.dart").write_text("")
    assert helper_files(bare) == ["bin/main.dart"]


def test_the_digest_covers_the_files_and_the_sdk_version(source: Path) -> None:
    first = digest(source, b"3.13.6\n")
    (source / "test").mkdir()
    (source / "test" / "a_test.dart").write_text("void main() {}\n")
    (source / ".dart_tool").mkdir()
    (source / ".dart_tool" / "package_config.json").write_text("{}")
    assert digest(source, b"3.13.6\n") == first
    assert digest(source, b"3.13.7\n") != first
    (source / "lib" / "cli.dart").write_text("void run() { }\n")
    assert digest(source, b"3.13.6\n") != first


def test_the_helper_is_compiled_once_into_the_cache(
    tmp_path: Path, source: Path, fake_dart: FakeDart
) -> None:
    cache = tmp_path / "cache"
    helper = Helper(source, cache)
    program = helper.program(fake_dart.sdk)
    version = fake_dart.sdk.version_file.read_bytes()
    assert program == cache / f"{digest(source, version)}.dill"
    assert program.read_text() == "kernel"
    assert helper.program(fake_dart.sdk) == program
    assert Helper(source, cache).program(fake_dart.sdk) == program
    pub, compile_, *rest = fake_dart.calls()
    assert rest == []
    assert pub[2:] == ["pub", "get", "--enforce-lockfile", "--offline"]
    build = Path(pub[0])
    assert build.name == "helper"
    assert build.parent.parent == cache
    assert compile_[2:6] == ["compile", "kernel", "bin/main.dart", "-o"]
    assert list(cache.iterdir()) == [program]


def test_bootstrap_compiles_online_and_a_deleted_snapshot_is_rebuilt(
    tmp_path: Path, source: Path, fake_dart: FakeDart
) -> None:
    helper = Helper(source, tmp_path / "cache")
    program = helper.program(fake_dart.sdk, offline=False)
    program.unlink()
    assert helper.program(fake_dart.sdk) == program
    assert program.is_file()
    gets = [call[2:] for call in fake_dart.calls() if call[2] == "pub"]
    assert gets == [
        ["pub", "get", "--enforce-lockfile"],
        ["pub", "get", "--enforce-lockfile", "--offline"],
    ]


def test_a_missing_helper_or_version_file_is_a_tool_failure(
    tmp_path: Path, source: Path, fake_dart: FakeDart
) -> None:
    with pytest.raises(ToolFailedError, match="the helper is missing at"):
        Helper(tmp_path / "nowhere", tmp_path / "cache").program(fake_dart.sdk)
    fake_dart.sdk.version_file.unlink()
    with pytest.raises(ToolFailedError, match="the SDK has no version file"):
        Helper(source, tmp_path / "cache").program(fake_dart.sdk)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("exit 0\n", "`dart compile kernel` wrote no snapshot"),
        ("echo broken >&2\nexit 254\n", "exit status 254: broken"),
    ],
)
def test_a_failed_build_leaves_no_snapshot(
    tmp_path: Path, source: Path, fake_dart: FakeDart, body: str, message: str
) -> None:
    fake_dart.handle("compile", body)
    cache = tmp_path / "cache"
    with pytest.raises(ToolFailedError, match=message):
        Helper(source, cache).program(fake_dart.sdk)
    assert list(cache.iterdir()) == []


def test_a_request_runs_the_compiled_helper_without_dart_options(
    tmp_path: Path, source: Path, fake_dart: FakeDart
) -> None:
    fake_dart.handle("dill", 'cp "$3" "$(dirname "$0")/request.json"\n' + ANSWER)
    helper = Helper(source, tmp_path / "cache")
    work = tmp_path / "work"
    answer = helper.run(fake_dart.sdk, "structure", {"files": []}, work=work, cwd=tmp_path)
    assert answer == {"protocol": 1, "files": []}
    call = fake_dart.calls()[-1]
    assert call[1:3] == [str(helper.program(fake_dart.sdk)), "structure"]
    assert Path(call[3]).parent == work / "dart-requests"
    request = json.loads((fake_dart.root / "handlers" / "request.json").read_text())
    assert request == {"protocol": 1, "files": []}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("echo 'bad request' >&2\nexit 2\n", "exit status 2: bad request"),
        ("echo '{\"files\": []}'\n", "structure answered without protocol 1"),
        ("echo 'not json'\n", "malformed JSON"),
    ],
)
def test_a_failed_request_is_a_tool_failure(
    tmp_path: Path, source: Path, fake_dart: FakeDart, body: str, message: str
) -> None:
    fake_dart.handle("dill", body)
    helper = Helper(source, tmp_path / "cache")
    with pytest.raises(ToolFailedError, match=message):
        helper.run(fake_dart.sdk, "structure", {}, work=tmp_path / "work", cwd=tmp_path)
