"""Package manifests: which profile a directory declares and whether it is a workspace."""

from __future__ import annotations

import configparser
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

import yaml

from quality import strictjson
from quality.policy import Profile

MAX_MANIFEST_BYTES = 1024 * 1024
# A setup.py declares a package through setuptools or distutils; other modules may share the name.
SETUP_IMPORT = re.compile(r"^\s*(?:from|import)\s+(?:setuptools|distutils)\b", re.MULTILINE)

type Document = dict[str, object]


@dataclass(frozen=True, slots=True)
class Manifest:
    """A manifest file; `package` is false for files that only configure tools."""

    path: str
    profile: Profile
    package: bool
    workspace: bool

    @property
    def directory(self) -> str:
        """The repository-relative directory of the manifest ('.' for the root)."""
        parent = str(PurePosixPath(self.path).parent)
        return "." if parent in {"", "."} else parent


class ManifestError(ValueError):
    """A manifest file cannot be parsed."""


def _mapping(value: object) -> Document:
    return cast(Document, value) if isinstance(value, dict) else {}


def _pyproject(text: str) -> tuple[bool, bool]:
    document = tomllib.loads(text)
    uv = _mapping(_mapping(document.get("tool")).get("uv"))
    return "project" in document, "workspace" in uv


def _setup_cfg(text: str) -> tuple[bool, bool]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    return parser.has_section("metadata"), False


def _package_json(text: str) -> tuple[bool, bool]:
    document = _mapping(strictjson.loads(text))
    return True, "workspaces" in document


def _pubspec(text: str) -> tuple[bool, bool]:
    document = _mapping(yaml.safe_load(text))
    return True, "workspace" in document


def _composer(text: str) -> tuple[bool, bool]:
    strictjson.loads(text)
    return True, False


def _setup_py(text: str) -> tuple[bool, bool]:
    return SETUP_IMPORT.search(text) is not None, False


PARSERS: dict[str, tuple[Profile, Callable[[str], tuple[bool, bool]]]] = {
    "pyproject.toml": (Profile.PYTHON, _pyproject),
    "setup.cfg": (Profile.PYTHON, _setup_cfg),
    "setup.py": (Profile.PYTHON, _setup_py),
    "package.json": (Profile.TYPESCRIPT, _package_json),
    "pubspec.yaml": (Profile.DART, _pubspec),
    "composer.json": (Profile.PHP, _composer),
}


def is_manifest(path: str) -> bool:
    """Whether a path names a recognized manifest."""
    return PurePosixPath(path).name in PARSERS


def parse_manifest(path: str, data: bytes) -> Manifest:
    """Parse a manifest without executing it; raise `ManifestError` when unreadable."""
    profile, parser = PARSERS[PurePosixPath(path).name]
    if len(data) > MAX_MANIFEST_BYTES:
        msg = f"manifest exceeds {MAX_MANIFEST_BYTES} bytes"
        raise ManifestError(msg)
    try:
        package, workspace = parser(data.decode("utf-8"))
    except (
        UnicodeDecodeError,
        ValueError,
        RecursionError,
        configparser.Error,
        yaml.YAMLError,
    ) as exc:
        msg = f"cannot parse manifest: {exc}".splitlines()[0]
        raise ManifestError(msg) from exc
    return Manifest(path=path, profile=profile, package=package, workspace=workspace)
