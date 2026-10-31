import pytest

from quality.discovery import Manifest, ManifestError, parse_manifest
from quality.discovery.manifests import MAX_MANIFEST_BYTES, is_manifest
from quality.policy import Profile

DEEP = b"[" * 10_000 + b"]" * 10_000


@pytest.mark.parametrize(
    ("path", "text", "profile", "package", "workspace"),
    [
        ("pyproject.toml", '[project]\nname = "a"\n', Profile.PYTHON, True, False),
        ("pyproject.toml", "[tool.uv.workspace]\nmembers = []\n", Profile.PYTHON, False, True),
        ("pyproject.toml", "[tool.ruff]\nline-length = 100\n", Profile.PYTHON, False, False),
        ("pyproject.toml", "tool = 1\n", Profile.PYTHON, False, False),
        ("setup.cfg", "[metadata]\nname = a\n", Profile.PYTHON, True, False),
        ("setup.cfg", "[flake8]\nmax-line-length = 100\n", Profile.PYTHON, False, False),
        (
            "setup.py",
            "from setuptools import setup\nraise SystemExit\n",
            Profile.PYTHON,
            True,
            False,
        ),
        ("setup.py", "import distutils.core\n", Profile.PYTHON, True, False),
        ("src/a/setup.py", "def setup() -> None: ...\n", Profile.PYTHON, False, False),
        ("setup.py", "# no setuptools here\n", Profile.PYTHON, False, False),
        ("web/package.json", '{"name": "web"}', Profile.TYPESCRIPT, True, False),
        ("package.json", '{"workspaces": ["apps/*"]}', Profile.TYPESCRIPT, True, True),
        ("package.json", "[]", Profile.TYPESCRIPT, True, False),
        ("pubspec.yaml", "name: app\n", Profile.DART, True, False),
        ("pubspec.yaml", "name: root\nworkspace: [pkg]\n", Profile.DART, True, True),
        ("composer.json", '{"name": "a/b"}', Profile.PHP, True, False),
    ],
)
def test_parses_manifests_without_running_them(
    path: str, text: str, profile: Profile, package: bool, workspace: bool
) -> None:
    assert parse_manifest(path, text.encode()) == Manifest(path, profile, package, workspace)


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("pyproject.toml", b"[project\n"),
        ("setup.cfg", b"name = a\n"),
        ("package.json", b"{"),
        ("pubspec.yaml", b"name: [\n"),
        ("composer.json", b"{,}"),
        ("package.json", b"\xff"),
        ("package.json", b" " * (MAX_MANIFEST_BYTES + 1)),
        ("package.json", DEEP),
        ("pyproject.toml", b"a = " + DEEP),
        ("pubspec.yaml", DEEP),
    ],
    ids=[
        "toml",
        "ini",
        "json",
        "yaml",
        "composer",
        "utf8",
        "oversized",
        "deep-json",
        "deep-toml",
        "deep-yaml",
    ],
)
def test_unreadable_manifests_raise(path: str, data: bytes) -> None:
    with pytest.raises(ManifestError, match="manifest"):
        parse_manifest(path, data)


def test_manifest_directory_is_relative_to_the_repository() -> None:
    assert Manifest("pyproject.toml", Profile.PYTHON, True, False).directory == "."
    assert Manifest("apps/web/package.json", Profile.TYPESCRIPT, True, False).directory == (
        "apps/web"
    )


def test_recognizes_manifests_by_name() -> None:
    assert is_manifest("apps/api/pyproject.toml")
    assert not is_manifest("docs/package.json.md")
