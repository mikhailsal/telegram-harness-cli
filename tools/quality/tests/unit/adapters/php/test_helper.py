import json
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.php.helper import HELPER, MAIN, MEMORY, Helper, digest, helper_files
from quality.testkit.phpfakes import FakePhp, write_helper

INSTALLED = "mkdir -p vendor\necho '<?php' > vendor/autoload.php\n"
ANSWER = 'printf \'{"protocol": 1, "files": []}\'\n'


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return write_helper(tmp_path / "helper")


@pytest.fixture
def composer(fake_php: FakePhp) -> FakePhp:
    fake_php.handle("composer", INSTALLED)
    return fake_php


def test_the_helper_lives_in_the_core() -> None:
    assert (HELPER / "composer.json").is_file()
    assert (HELPER / "composer.lock").is_file()
    assert (HELPER / MAIN).is_file()


def test_the_installation_is_built_from_the_manifests_and_the_program_files(
    tmp_path: Path, source: Path
) -> None:
    (source / "src" / "Rules").mkdir()
    (source / "src" / "Rules" / "A.php").write_text("<?php\n")
    (source / "tests").mkdir()
    (source / "tests" / "ATest.php").write_text("<?php\n")
    (source / "vendor").mkdir()
    (source / "vendor" / "autoload.php").write_text("<?php\n")
    assert helper_files(source) == [
        "composer.json",
        "composer.lock",
        "bin/main.php",
        "src/Cli.php",
        "src/Rules/A.php",
    ]
    bare = tmp_path / "bare"
    (bare / "bin").mkdir(parents=True)
    (bare / "bin" / "main.php").write_text("")
    assert helper_files(bare) == ["bin/main.php"]


def test_the_digest_covers_the_files_and_the_php_version(source: Path) -> None:
    first = digest(source, "8.4.14")
    (source / "tests").mkdir()
    (source / "tests" / "ATest.php").write_text("<?php\n")
    assert digest(source, "8.4.14") == first
    assert digest(source, "8.4.15") != first
    (source / "src" / "Cli.php").write_text("<?php // changed\n")
    assert digest(source, "8.4.14") != first


def test_the_helper_is_installed_once_into_the_cache(
    tmp_path: Path, source: Path, composer: FakePhp
) -> None:
    cache = tmp_path / "cache"
    helper = Helper(source, cache)
    program = helper.program(composer.php)
    target = cache / digest(source, composer.php.version)
    assert program == target / MAIN
    assert (target / "vendor" / "autoload.php").is_file()
    assert helper.program(composer.php) == program
    assert Helper(source, cache).program(composer.php) == program
    ((build, *argv),) = composer.calls()
    assert Path(build).name == "helper"
    assert Path(build).parent.parent == cache
    assert argv[-5:] == ["install", "--no-progress", "--no-scripts", "--no-plugins", "--no-dev"]
    assert sorted(path.name for path in cache.iterdir()) == [target.name]


def test_bootstrap_installs_online_and_a_deleted_installation_is_rebuilt(
    tmp_path: Path, source: Path, composer: FakePhp
) -> None:
    (composer.root / "handlers" / "composer.sh").write_text(
        INSTALLED + 'echo "${COMPOSER_DISABLE_NETWORK:-online}" >> ../../network.log\n'
    )
    helper = Helper(source, tmp_path / "cache")
    program = helper.program(composer.php, offline=False)
    program.unlink()
    (program.parent.parent / "stale").write_text("")
    assert helper.program(composer.php) == program
    assert not (program.parent.parent / "stale").exists()
    assert (tmp_path / "cache" / "network.log").read_text().split() == ["online", "1"]


def test_a_missing_helper_is_a_tool_failure(tmp_path: Path, composer: FakePhp) -> None:
    with pytest.raises(ToolFailedError, match="the helper is missing at"):
        Helper(tmp_path / "nowhere", tmp_path / "cache").program(composer.php)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("exit 0\n", "Composer installed no autoloader in"),
        ("echo 'not in the cache' >&2\nexit 1\n", "composer install: php helper: not in"),
    ],
)
def test_a_failed_installation_leaves_nothing_behind(
    tmp_path: Path, source: Path, fake_php: FakePhp, body: str, message: str
) -> None:
    fake_php.handle("composer", body)
    cache = tmp_path / "cache"
    with pytest.raises(ToolFailedError, match=message):
        Helper(source, cache).program(fake_php.php)
    assert list(cache.iterdir()) == []


def test_a_concurrent_installation_of_the_same_digest_is_used(
    tmp_path: Path, source: Path, composer: FakePhp
) -> None:
    cache = tmp_path / "cache"
    target = cache / digest(source, composer.php.version)
    # The rename finds a complete installation that another run made meanwhile.
    composer.handle("composer", f"{INSTALLED}mkdir -p '{target}/bin'\ntouch '{target}/{MAIN}'\n")
    assert Helper(source, cache).program(composer.php) == target / MAIN
    (target / MAIN).unlink()
    (target / "bin").rmdir()
    target.rmdir()
    composer.handle("composer", f"{INSTALLED}touch '{target}'\n")
    with pytest.raises(ToolFailedError, match="cannot install the helper at"):
        Helper(source, cache).program(composer.php)


def test_a_request_runs_the_installed_helper_with_more_memory(
    tmp_path: Path, source: Path, composer: FakePhp
) -> None:
    composer.handle("main.php", f"cp \"$3\" '{composer.root}/request.json'\n" + ANSWER)
    helper = Helper(source, tmp_path / "cache")
    work = tmp_path / "work"
    answer = helper.run(composer.php, "structure", {"files": []}, work=work, cwd=tmp_path)
    assert answer == {"protocol": 1, "files": []}
    cwd, *argv = composer.calls()[-1]
    assert cwd == str(tmp_path)
    assert argv[2:4] == ["-d", MEMORY]
    assert argv[4:6] == [str(helper.program(composer.php)), "structure"]
    assert Path(argv[6]).parent == work / "php-requests"
    request = json.loads((composer.root / "request.json").read_text())
    assert request == {"protocol": 1, "files": []}
    assert (work / "php-home" / "helper").is_dir()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("echo 'Fatal error' >&2\nexit 255\n", "exit status 255: Fatal error"),
        ("echo '{\"files\": []}'\n", "structure answered without protocol 1"),
        ("echo 'not json'\n", "malformed JSON"),
        (
            'printf \'{"protocol": 1, "failure": "cannot read the request"}\'\n',
            "structure failed: cannot read the request",
        ),
    ],
)
def test_a_failed_request_is_a_tool_failure(
    tmp_path: Path, source: Path, composer: FakePhp, body: str, message: str
) -> None:
    composer.handle("main.php", body)
    helper = Helper(source, tmp_path / "cache")
    with pytest.raises(ToolFailedError, match=message):
        helper.run(composer.php, "structure", {}, work=tmp_path / "work", cwd=tmp_path)
