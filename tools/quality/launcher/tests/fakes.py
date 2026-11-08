"""In-memory uv releases and recorded effects for the launcher tests."""

import hashlib
import io
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, NoReturn

import pytest

from quality_launcher_uv import Artifact, Uv

URL = "https://example.invalid/uv-x86_64.tar.gz"
MEMBER = "uv-x86_64/uv"
BINARY = b"#!/bin/sh\necho fake uv\n"
INHERITED = {
    "VIRTUAL_ENV": "/venv",
    "PYTHONPATH": "/path",
    "PYTHONHOME": "/home",
    "UV_PROJECT_ENVIRONMENT": "/environment",
}


def tarball(*members: tuple[str, bytes | None]) -> bytes:
    """Return an uncompressed tar archive; a member without a body is a symbolic link."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, body in members:
            info = tarfile.TarInfo(name)
            if body is None:
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


RELEASE = tarball(("uv-x86_64/README.md", b"docs\n"), (MEMBER, BINARY))


def release(data: bytes = RELEASE, *, sha256: str | None = None, member: str = MEMBER) -> Uv:
    """Return a uv pin whose x86_64 artifact is `data`."""
    artifact = Artifact(URL, sha256 or hashlib.sha256(data).hexdigest(), member)
    return Uv("9.9.9", "uv", {"x86_64": artifact})


def manifest(uv: Uv) -> str:
    """Return the toolchain manifest text that pins `uv`."""
    lines = ["[tools.uv]", f'version = "{uv.version}"', f'binary = "{uv.binary}"']
    for arch, artifact in uv.artifacts.items():
        lines += [
            f"[tools.uv.artifacts.{arch}]",
            f'url = "{artifact.url}"',
            f'sha256 = "{artifact.sha256}"',
            'packaging = "tar"',
            f'member = "{artifact.member}"',
        ]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Server:
    """Serve one archive at `URL` and count the downloads."""

    data: bytes = RELEASE
    downloads: list[str] = field(default_factory=list[str])

    def __call__(self, url: str) -> IO[bytes]:
        assert url == URL
        self.downloads.append(url)
        return io.BytesIO(self.data)


def no_network(url: str) -> IO[bytes]:
    """Fail the test on any download."""
    pytest.fail(f"unexpected download of {url}")


@dataclass(frozen=True)
class Run:
    """One recorded command."""

    argv: list[str]
    cwd: Path
    env: dict[str, str]
    capture: bool


@dataclass
class Commands:
    """Record commands and answer them with scripted exit statuses."""

    statuses: list[int]
    output: str = ""
    runs: list[Run] = field(default_factory=list[Run])

    def __call__(
        self, argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], capture: bool
    ) -> tuple[int, str]:
        self.runs.append(Run(list(argv), cwd, dict(env), capture))
        return self.statuses.pop(0), self.output if capture else ""


class ExecutedError(Exception):
    """Raised by `execve` in place of replacing the test process."""

    def __init__(self, path: str, argv: list[str], env: Mapping[str, str]) -> None:
        super().__init__(path)
        self.path = path
        self.argv = argv
        self.env = dict(env)


def execve(path: str, argv: list[str], env: Mapping[str, str]) -> NoReturn:
    """Record the exec instead of performing it."""
    raise ExecutedError(path, argv, env)
