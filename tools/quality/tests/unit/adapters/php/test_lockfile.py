import json
from pathlib import Path

import pytest

from quality.adapters import native
from quality.adapters.php.lockfile import Lock, check_lock, load_lock
from quality.boundedio import ReportError
from quality.testkit.phpfakes import PINS, composer_lock, package

TAR = "https://example.com/a.tar"


def problems(directory: Path, snapshot: Path) -> tuple[Lock | None, list[tuple[str, str]]]:
    found: list[tuple[str, str]] = []
    lock = check_lock(
        directory, snapshot, PINS, lambda file, message: found.append((file, message))
    )
    return lock, found


def write(directory: Path, document: object) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "composer.lock").write_text(json.dumps(document))
    return directory


def test_a_lock_with_the_pins_is_accepted(snapshot: Path) -> None:
    directory = snapshot / "app"
    directory.mkdir()
    extra = [
        package("psr/log", "3.0.2"),
        {"name": "local/lib", "dist": {"type": "path", "url": "lib"}},
    ]
    (directory / "composer.lock").write_text(composer_lock(extra=extra))
    lock, found = problems(directory, snapshot)
    assert found == []
    assert lock is not None
    assert lock.directory == directory
    assert set(lock.packages) == {*PINS, "psr/log", "local/lib"}
    assert lock.paths() == [("local/lib", "lib")]


def test_a_missing_or_unusable_lock_is_reported(snapshot: Path) -> None:
    directory = snapshot / "app"
    directory.mkdir()
    assert problems(directory, snapshot) == (
        None,
        [
            (
                "composer.lock",
                "no composer.lock beside composer.json; run `composer update` and commit it",
            )
        ],
    )
    (directory / "composer.lock").write_text("[]")
    lock, found = problems(directory, snapshot)
    assert lock is None
    assert found == [
        ("composer.lock", "the lockfile is unusable: composer.lock does not hold a JSON object")
    ]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"packages": {}}, "'packages' is not a list"),
        ({"packages-dev": [3]}, "an entry of 'packages-dev' has no package name"),
        ({"packages": [{"name": 3}]}, "an entry of 'packages' has no package name"),
    ],
    ids=["section", "entry", "name"],
)
def test_lock_sections_must_list_named_packages(
    tmp_path: Path, document: object, message: str
) -> None:
    with pytest.raises(ReportError, match=f"^{message}$"):
        load_lock(write(tmp_path, document))


def test_a_v_prefix_of_a_locked_version_is_ignored(snapshot: Path) -> None:
    pins = {name: f"v{version}" for name, version in PINS.items()}
    directory = write(snapshot / "app", json.loads(composer_lock(pins)))
    assert problems(directory, snapshot)[1] == []


def test_each_pin_must_be_locked_exactly_from_an_https_zip_archive(snapshot: Path) -> None:
    pins = {**PINS, "phpstan/phpstan": "2.3.0"}
    del pins["friendsofphp/php-cs-fixer"]
    document = json.loads(composer_lock(pins))
    entries = {entry["name"]: entry for entry in document["packages-dev"]}
    entries["phpunit/phpunit"]["dist"]["type"] = "tar"
    entries["phpunit/php-code-coverage"]["dist"] = {"type": "zip", "url": "http://e.com/a.zip"}
    directory = write(snapshot / "app", document)
    found = [message for _, message in problems(directory, snapshot)[1]]

    def archive(name: str, version: str) -> str:
        return f"https://api.github.com/repos/{name}/zipball/{version}"

    https = "from a zip archive over HTTPS"
    assert found == [
        (
            f"phpunit/phpunit is locked at '13.4.3' from '{archive('phpunit/phpunit', '13.4.3')}'; "
            f"the profile pins 13.4.3 {https}"
        ),
        (
            "phpunit/php-code-coverage is locked at '14.4.1' from 'http://e.com/a.zip'; "
            f"the profile pins 14.4.1 {https}"
        ),
        (
            f"phpstan/phpstan is locked at '2.3.0' from '{archive('phpstan/phpstan', '2.3.0')}'; "
            f"the profile pins 2.3.1 {https}"
        ),
        (
            "the lockfile does not lock friendsofphp/php-cs-fixer; "
            "require `friendsofphp/php-cs-fixer: 3.95.27`"
        ),
        "phpunit/php-code-coverage is downloaded from 'http://e.com/a.zip', not over HTTPS",
    ]


def test_every_archive_and_source_comes_over_https(snapshot: Path) -> None:
    extra = [
        package("a/archive", "1.0.0", url="http://e.com/a.zip"),
        {**package("b/source", "1.0.0"), "source": {"type": "git", "url": "git@e.com:b.git"}},
        {**package("c/both", "1.0.0", url="ftp://e.com/c"), "source": {"url": "ftp://e.com/c"}},
        {"name": "d/meta", "version": "1.0.0", "type": "metapackage"},
    ]
    directory = write(snapshot / "app", json.loads(composer_lock(extra=extra)))
    assert [message for _, message in problems(directory, snapshot)[1]] == [
        "a/archive is downloaded from 'http://e.com/a.zip', not over HTTPS",
        "b/source is downloaded from 'git@e.com:b.git', not over HTTPS",
        "c/both is downloaded from 'ftp://e.com/c', not over HTTPS",
    ]


def test_path_packages_stay_inside_the_repository(snapshot: Path) -> None:
    extra = [
        {"name": "in/side", "dist": {"type": "path", "url": "../lib"}},
        {"name": "out/side", "dist": {"type": "path", "url": "../../outside"}},
        {"name": "home/dir", "dist": {"type": "path", "url": "~/lib"}},
    ]
    directory = write(snapshot / "app", json.loads(composer_lock(extra=extra)))
    assert [message for _, message in problems(directory, snapshot)[1]] == [
        "out/side is installed from '../../outside', outside the repository",
        "home/dir is installed from '~/lib', outside the repository",
    ]


def test_the_manifest_must_pin_every_required_package(snapshot: Path) -> None:
    directory = write(snapshot / "app", json.loads(composer_lock()))
    pins = {name: version for name, version in PINS.items() if name != "phpstan/phpstan"}
    with pytest.raises(native.ToolFailedError) as raised:
        check_lock(directory, snapshot, pins, lambda file, message: None)
    assert (
        raised.value.diagnostic.message == "php: toolchains.toml pins no version of phpstan/phpstan"
    )
