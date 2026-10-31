"""`toolchains.toml`: pinned versions, download URLs, SHA-256 checksums, and package pins.

Standalone tools are downloaded and verified by the core. Package pins are the
exact versions a profile requires in a component's own lockfile; the package
manager installs them from that lockfile (decision 0011 for npm). Runtimes are
system programs the core does not install, such as PHP and its Xdebug
extension, with the minor version a profile requires (decision 0013).
"""

from __future__ import annotations

import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

MANIFEST = Path(__file__).resolve().parents[3] / "toolchains.toml"

type Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
type HttpsUrl = Annotated[str, StringConstraints(pattern=r"^https://[^\s]+$")]
type Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*$")]
# Relative paths whose segments are not "." or "..": every segment has a non-dot character.
type Member = Annotated[
    str, StringConstraints(pattern=r"^[\w.+-]*[\w+-][\w.+-]*(?:/[\w.+-]*[\w+-][\w.+-]*)*$")
]
# Package names of npm, optionally scoped, of pub, and of Composer (`vendor/name`):
# lowercase, no leading dot or underscore.
type PackageName = Annotated[
    str, StringConstraints(pattern=r"^(?:@?[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")
]
type ExactVersion = Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
TOOL_VERSION = r"^\d+(?:\.\d+)+$"
# A vendor checksum file: an HTTPS URL, or `{url}` and a suffix, with `{version}` placeholders.
type ChecksumTemplate = Annotated[str, StringConstraints(pattern=r"^(?:https://|\{url\})[^\s]+$")]
type MinorVersion = Annotated[str, StringConstraints(pattern=r"^\d+\.\d+$")]


class Packaging(StrEnum):
    """How a download holds the executable.

    `tree` archives are extracted whole below their single top directory, the
    `member`, for tools such as Node.js that need their libraries beside them.
    """

    BINARY = "binary"
    TAR = "tar"
    ZIP = "zip"
    TREE = "tree"


class Architecture(StrEnum):
    """Supported Linux machine architectures."""

    X86_64 = "x86_64"
    AARCH64 = "aarch64"


class Strict(BaseModel):
    """Models reject unknown keys and are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Artifact(Strict):
    """One platform download and the archive member that is the executable or the tree."""

    url: HttpsUrl
    sha256: Sha256
    packaging: Packaging
    member: Member | None = None

    @model_validator(mode="after")
    def _member_matches_packaging(self) -> Self:
        if (self.packaging is Packaging.BINARY) != (self.member is None):
            msg = "'member' is required for archives and not allowed for binary downloads"
            raise ValueError(msg)
        if self.packaging is Packaging.TREE and "/" in (self.member or ""):
            msg = "the 'member' of a tree archive is its single top directory"
            raise ValueError(msg)
        return self


class Extra(Strict):
    """A verified data file installed next to the executable, such as a rule set."""

    url: HttpsUrl
    sha256: Sha256


class Tool(Strict):
    """A pinned tool with one artifact per machine architecture and optional data files.

    `executable` is the program's path inside the installed directory; it
    defaults to `binary`, the name of the program. `checksums` names the
    vendor's checksum file, from which `quality tools update` takes the
    checksums of another release.
    """

    version: Annotated[str, StringConstraints(pattern=TOOL_VERSION)]
    binary: Name
    executable: Member | None = None
    checksums: ChecksumTemplate | None = None
    artifacts: dict[Architecture, Artifact] = Field(min_length=1)
    extras: dict[Member, Extra] = Field(default_factory=dict[Member, Extra])

    @property
    def program(self) -> str:
        """The executable's path relative to the installed directory."""
        return self.executable or self.binary


class Runtime(Strict):
    """A system program the core does not install, and the minor version it must have."""

    version: MinorVersion


class Manifest(Strict):
    """The complete toolchain manifest."""

    schema_version: Literal[1]
    tools: dict[Name, Tool]
    packages: dict[Name, dict[PackageName, ExactVersion]] = Field(
        default_factory=dict[Name, dict[PackageName, ExactVersion]]
    )
    runtimes: dict[Name, Runtime] = Field(default_factory=dict[Name, Runtime])


class ManifestError(Exception):
    """The manifest is missing or invalid."""


def parse_manifest(data: bytes) -> Manifest:
    """Parse and validate a manifest document."""
    try:
        return Manifest.model_validate(tomllib.loads(data.decode("utf-8")))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError) as error:
        msg = f"invalid toolchain manifest: {error}"
        raise ManifestError(msg) from error


def load_manifest(path: Path = MANIFEST) -> Manifest:
    """Load the manifest shipped next to the core."""
    try:
        return parse_manifest(path.read_bytes())
    except OSError as error:
        msg = f"cannot read toolchain manifest {path}: {error.strerror}"
        raise ManifestError(msg) from error
