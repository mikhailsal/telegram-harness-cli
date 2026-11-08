"""Read the uv pin from the toolchain manifest and install that uv.

This module belongs to the launcher, so it uses the standard library of Python 3.10
only. The manifest reader accepts the small TOML subset the uv tables use.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import tarfile
import tempfile
import urllib.parse
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import IO

DOWNLOAD_TIMEOUT = 120.0
DOWNLOAD_LIMIT = 256 * 1024 * 1024
CHUNK = 1024 * 1024
MAX_REDIRECTS = 5
REDIRECTS = frozenset({301, 302, 303, 307, 308})
MACHINES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}
UV_TABLE = "tools.uv"
ARTIFACTS = UV_TABLE + ".artifacts."
HEADER = re.compile(r"\[([^\[\]]*)\]\s*(?:#.*)?")
STRING = re.compile(r'([A-Za-z0-9_-]+)\s*=\s*"([^"\\]*)"\s*(?:#.*)?')

Opener = Callable[[str], IO[bytes]]


class LauncherError(Exception):
    """An environment problem; the launcher exits with status 3."""


@dataclass(frozen=True)
class Artifact:
    """A verified uv download for one machine architecture."""

    url: str
    sha256: str
    member: str


@dataclass(frozen=True)
class Uv:
    """The pinned uv release."""

    version: str
    binary: str
    artifacts: Mapping[str, Artifact]

    def path(self, cache: Path) -> Path:
        """Return where this uv is installed under the cache."""
        return cache / "toolchains" / f"uv-{self.version}" / self.binary


def read_tables(text: str, prefix: str) -> dict[str, dict[str, str]]:
    """Read the string keys of the TOML tables named `prefix` or below it.

    Inside those tables only `key = "string"` lines, comments, and blank lines are
    accepted; every other table is skipped.
    """
    tables: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line.startswith("["):
            header = HEADER.fullmatch(line)
            name = header.group(1).strip() if header else ""
            wanted = name == prefix or name.startswith(prefix + ".")
            current = tables.setdefault(name, {}) if wanted else None
        elif current is not None and line and not line.startswith("#"):
            pair = STRING.fullmatch(line)
            if pair is None:
                msg = f'toolchains.toml line {number}: expected key = "string"'
                raise LauncherError(msg)
            current[pair.group(1)] = pair.group(2)
    return tables


def _value(table: Mapping[str, str], name: str, key: str) -> str:
    if not (value := table.get(key)):
        msg = f"toolchains.toml: [{name}] needs a non-empty {key}"
        raise LauncherError(msg)
    return value


def load_uv(path: Path) -> Uv:
    """Read the uv pin from the toolchain manifest."""
    if not path.is_file():
        msg = f"{path} is missing; the launcher must stay in tools/quality/launcher"
        raise LauncherError(msg)
    tables = read_tables(path.read_text(encoding="utf-8"), UV_TABLE)
    if UV_TABLE not in tables:
        msg = f"toolchains.toml has no [{UV_TABLE}] table"
        raise LauncherError(msg)
    artifacts: dict[str, Artifact] = {}
    for name, table in tables.items():
        if not name.startswith(ARTIFACTS):
            continue
        if _value(table, name, "packaging") != "tar":
            msg = f"toolchains.toml: [{name}] must use tar packaging"
            raise LauncherError(msg)
        keys = ("url", "sha256", "member")
        artifacts[name.removeprefix(ARTIFACTS)] = Artifact(*(_value(table, name, k) for k in keys))
    tool = tables[UV_TABLE]
    return Uv(_value(tool, UV_TABLE, "version"), _value(tool, UV_TABLE, "binary"), artifacts)


def machine(raw: str) -> str:
    """Normalize a machine name to an artifact architecture."""
    if (arch := MACHINES.get(raw.lower())) is None:
        msg = f"unsupported machine architecture: {raw}"
        raise LauncherError(msg)
    return arch


def https_open(url: str) -> IO[bytes]:
    """Open an HTTPS URL, following at most a few HTTPS redirects; other schemes fail."""
    for _ in range(MAX_REDIRECTS + 1):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            msg = f"refusing to download from a non-HTTPS URL: {url}"
            raise LauncherError(msg)
        connection = http.client.HTTPSConnection(
            parts.hostname, parts.port, timeout=DOWNLOAD_TIMEOUT
        )
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        connection.request("GET", path or "/", headers={"User-Agent": "quality-launcher"})
        response = connection.getresponse()
        if response.status == HTTPStatus.OK:
            return response
        location = response.getheader("Location")
        response.close()
        connection.close()
        if response.status not in REDIRECTS or not location:
            msg = f"download failed with HTTP {response.status}: {url}"
            raise LauncherError(msg)
        url = urllib.parse.urljoin(url, location)
    msg = f"too many redirects: {url}"
    raise LauncherError(msg)


def download(artifact: Artifact, archive: Path, binary: Path, opener: Opener) -> None:
    """Stream and verify the archive, then copy only its named regular member to `binary`."""
    digest = hashlib.sha256()
    total = 0
    with closing(opener(artifact.url)) as response, archive.open("wb") as out:
        while chunk := response.read(CHUNK):
            total += len(chunk)
            if total > DOWNLOAD_LIMIT:
                msg = f"download exceeds {DOWNLOAD_LIMIT} bytes"
                raise LauncherError(msg)
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != artifact.sha256:
        msg = f"checksum mismatch: expected {artifact.sha256}, got {digest.hexdigest()}"
        raise LauncherError(msg)
    with tarfile.open(archive) as tar:
        info = tar.getmember(artifact.member)
        stream = tar.extractfile(info) if info.isfile() else None
        if stream is None:
            msg = f"archive member {artifact.member} is not a regular file"
            raise LauncherError(msg)
        with stream:
            binary.write_bytes(stream.read())
    binary.chmod(0o755)


def executable(path: Path) -> bool:
    """Return whether `path` is an executable regular file."""
    return path.is_file() and os.access(path, os.X_OK)


def install_uv(uv: Uv, cache: Path, arch: str, opener: Opener) -> Path:
    """Install the pinned uv from a verified download; idempotent."""
    if executable(path := uv.path(cache)):
        return path
    if (artifact := uv.artifacts.get(arch)) is None:
        msg = f"uv {uv.version} has no artifact for {arch}"
        raise LauncherError(msg)
    toolchains = path.parent.parent
    toolchains.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=toolchains, prefix=".install-") as temporary:
        staging = Path(temporary)
        final = staging / path.parent.name
        final.mkdir()
        try:
            download(artifact, staging / "download", final / uv.binary, opener)
        except (LauncherError, OSError, KeyError, tarfile.TarError) as error:
            msg = f"cannot install uv {uv.version}: {error}"
            raise LauncherError(msg) from error
        if path.parent.exists():
            # A directory without a usable executable is stale; it leaves with the staging area.
            path.parent.rename(staging / "stale")
        final.rename(path.parent)
    return path
