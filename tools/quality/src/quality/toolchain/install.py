"""Locate pinned tools in the cache and install them from verified downloads."""

from __future__ import annotations

import hashlib
import http.client
import os
import platform
import tarfile
import tempfile
import urllib.parse
import zipfile
from collections.abc import Callable, Mapping
from contextlib import closing
from http import HTTPStatus
from pathlib import Path
from typing import IO

from quality.diagnostics import Diagnostic, Kind, Rule
from quality.toolchain.archive import place
from quality.toolchain.manifest import (
    Architecture,
    Artifact,
    Extra,
    ManifestError,
    Tool,
)

MISSING = Rule(
    id="tools.missing",
    title="A pinned tool is not installed",
    requirement="R02",
    explanation="Checks use pinned tools from the local cache; nothing is fetched during a check.",
    remedy="Run `quality tools install <name>` to install the pinned version.",
    kind=Kind.ENVIRONMENT,
)
INSTALL_FAILED = Rule(
    id="tools.install-failed",
    title="A pinned tool could not be installed",
    requirement="R02",
    explanation="Downloads must match the pinned SHA-256 checksum before anything is installed.",
    remedy="Check the network and the manifest entry, then retry.",
    kind=Kind.ENVIRONMENT,
)
RULES = (MISSING, INSTALL_FAILED)

DOWNLOAD_LIMIT = 512 * 1024 * 1024
DOWNLOAD_TIMEOUT = 120.0
CHUNK = 1024 * 1024
MAX_REDIRECTS = 5
REDIRECTS = frozenset({301, 302, 303, 307, 308})
MACHINES = {
    "x86_64": Architecture.X86_64,
    "amd64": Architecture.X86_64,
    "aarch64": Architecture.AARCH64,
    "arm64": Architecture.AARCH64,
}

type Opener = Callable[[str], IO[bytes]]


class ToolchainError(Exception):
    """A tool is missing or cannot be installed."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


def cache_root(environ: Mapping[str, str] | None = None) -> Path:
    """Return `$XDG_CACHE_HOME/quality/toolchains`, defaulting to `~/.cache`."""
    env = os.environ if environ is None else environ
    base = env.get("XDG_CACHE_HOME") or str(Path(env.get("HOME", str(Path.home()))) / ".cache")
    return Path(base) / "quality" / "toolchains"


def python_root(environ: Mapping[str, str] | None = None) -> Path:
    """Return the directory of uv-managed interpreters, beside the tool cache."""
    return cache_root(environ).parent / "python"


def machine() -> Architecture | None:
    """Return the normalized machine architecture, if it is supported."""
    return MACHINES.get(platform.machine().lower())


def install_directory(name: str, tool: Tool, root: Path) -> Path:
    """Return the versioned directory that holds an installed tool."""
    return root / f"{name}-{tool.version}"


def tool_path(name: str, tool: Tool, root: Path) -> Path:
    """Return where the tool's executable lives once installed."""
    return install_directory(name, tool, root) / tool.program


def extra_path(name: str, tool: Tool, root: Path, extra: str) -> Path:
    """Return where an installed data file of the tool lives."""
    return install_directory(name, tool, root) / extra


def locate(name: str, tool: Tool, root: Path) -> Path:
    """Return the installed executable or raise `ToolchainError`."""
    path = tool_path(name, tool, root)
    if path.is_file() and os.access(path, os.X_OK):
        return path
    message = f"{name} {tool.version} is not installed (expected {path})"
    raise ToolchainError(MISSING.diagnostic(message))


def https_opener(url: str) -> IO[bytes]:
    """Open an HTTPS URL, following at most a few HTTPS redirects; other schemes fail."""
    for _ in range(MAX_REDIRECTS + 1):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            msg = f"refusing to download from a non-HTTPS URL: {url}"
            raise ManifestError(msg)
        connection = http.client.HTTPSConnection(
            parts.hostname, parts.port, timeout=DOWNLOAD_TIMEOUT
        )
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        connection.request("GET", path or "/", headers={"User-Agent": "quality-toolchain"})
        response = connection.getresponse()
        location = response.getheader("Location")
        if response.status in REDIRECTS and location:
            response.close()
            connection.close()
            url = urllib.parse.urljoin(url, location)
            continue
        if response.status != HTTPStatus.OK:
            response.close()
            connection.close()
            msg = f"download failed with HTTP {response.status}: {url}"
            raise ManifestError(msg)
        return response
    msg = f"too many redirects: {url}"
    raise ManifestError(msg)


def _download(artifact: Artifact | Extra, target: Path, opener: Opener) -> None:
    digest = hashlib.sha256()
    total = 0
    with closing(opener(artifact.url)) as response, target.open("wb") as out:
        while chunk := response.read(CHUNK):
            total += len(chunk)
            if total > DOWNLOAD_LIMIT:
                msg = f"download exceeds {DOWNLOAD_LIMIT} bytes"
                raise ManifestError(msg)
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != artifact.sha256:
        msg = f"checksum mismatch: expected {artifact.sha256}, got {digest.hexdigest()}"
        raise ManifestError(msg)


def install(name: str, tool: Tool, root: Path, *, opener: Opener = https_opener) -> Path:
    """Download, verify, and install the tool for this machine; idempotent."""
    path = tool_path(name, tool, root)
    if path.is_file() and os.access(path, os.X_OK):
        return path
    arch = machine()
    artifact = tool.artifacts.get(arch) if arch is not None else None
    if artifact is None:
        message = f"{name} {tool.version} has no artifact for {platform.machine()}"
        raise ToolchainError(INSTALL_FAILED.diagnostic(message))
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, prefix=".install-") as temporary:
        staging = Path(temporary)
        download = staging / "download"
        directory = install_directory(name, tool, root)
        final = staging / directory.name
        try:
            _download(artifact, download, opener)
            place(artifact, download, final, tool)
            for filename, extra in sorted(tool.extras.items()):
                _download(extra, final / filename, opener)
        except (ManifestError, OSError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
            message = f"cannot install {name} {tool.version}: {error}"
            raise ToolchainError(INSTALL_FAILED.diagnostic(message)) from error
        if directory.exists():
            # A directory without a usable executable is stale; it leaves with the staging area.
            directory.rename(staging / "stale")
        final.rename(directory)
    return path
