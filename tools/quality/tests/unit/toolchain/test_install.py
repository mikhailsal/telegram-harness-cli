import hashlib
import io
import os
import platform
import sys
import tarfile
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO

import pytest

from quality.toolchain import ToolchainError, extra_path, install, locate
from quality.toolchain.install import (
    https_opener,
    tool_path,
)
from quality.toolchain.manifest import Architecture, Artifact, Extra, Packaging, Tool

BINARY = b"#!/bin/sh\necho demo\n"
RULES = b"title = 'rules'\n"
# The package re-exports `install`, which shadows the module of the same name.
INSTALLER = sys.modules[https_opener.__module__]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tar_bytes(members: Mapping[str, bytes], *, directories: tuple[str, ...] = ()) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in directories:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("pkg/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    return buffer.getvalue()


def zip_bytes(members: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


def make_tool(
    download: bytes,
    packaging: Packaging,
    member: str | None = None,
    *,
    checksum: str | None = None,
    extras: bool = True,
) -> Tool:
    artifact = Artifact(
        url="https://example.invalid/demo",
        sha256=checksum or sha(download),
        packaging=packaging,
        member=member,
    )
    added = {"rules.toml": Extra(url="https://example.invalid/rules", sha256=sha(RULES))}
    return Tool(
        version="1.2.3",
        binary="demo",
        artifacts=dict.fromkeys(Architecture, artifact),
        extras=added if extras else {},
    )


def opener_for(download: bytes) -> Callable[[str], IO[bytes]]:
    files = {"https://example.invalid/demo": download, "https://example.invalid/rules": RULES}

    def opener(url: str) -> IO[bytes]:
        return io.BytesIO(files[url])

    return opener


def refuse(url: str) -> IO[bytes]:
    msg = f"unexpected download of {url}"
    raise AssertionError(msg)


@pytest.fixture(autouse=True)
def x86_64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")


def leftovers(root: Path) -> list[str]:
    return sorted(path.name for path in root.iterdir()) if root.exists() else []


def test_paths_are_versioned(tmp_path: Path) -> None:
    tool = make_tool(BINARY, Packaging.BINARY)
    assert tool_path("demo", tool, tmp_path) == tmp_path / "demo-1.2.3" / "demo"
    assert extra_path("demo", tool, tmp_path, "rules.toml") == tmp_path / "demo-1.2.3/rules.toml"


def test_locate_requires_an_installed_executable(tmp_path: Path) -> None:
    tool = make_tool(BINARY, Packaging.BINARY)
    with pytest.raises(ToolchainError) as raised:
        locate("demo", tool, tmp_path)
    assert raised.value.diagnostic.rule == "tools.missing"
    expected = tmp_path / "demo-1.2.3" / "demo"
    assert raised.value.diagnostic.message == f"demo 1.2.3 is not installed (expected {expected})"
    expected.parent.mkdir()
    expected.write_bytes(BINARY)
    expected.chmod(0o644)
    with pytest.raises(ToolchainError):
        locate("demo", tool, tmp_path)
    expected.chmod(0o755)
    assert locate("demo", tool, tmp_path) == expected


@pytest.mark.parametrize(
    ("download", "packaging", "member"),
    [
        (BINARY, Packaging.BINARY, None),
        (
            tar_bytes({"pkg/demo": BINARY, "pkg/README": b"x", "../escape": b"x"}),
            Packaging.TAR,
            "pkg/demo",
        ),
        (zip_bytes({"pkg/demo": BINARY, "pkg/README": b"x"}), Packaging.ZIP, "pkg/demo"),
    ],
    ids=["binary", "tar", "zip"],
)
def test_install_verifies_and_copies_only_the_member(
    tmp_path: Path, download: bytes, packaging: Packaging, member: str | None
) -> None:
    root = tmp_path / "cache"
    tool = make_tool(download, packaging, member)
    path = install("demo", tool, root, opener=opener_for(download))
    assert path == root / "demo-1.2.3" / "demo"
    assert path.read_bytes() == BINARY
    assert os.access(path, os.X_OK)
    assert sorted(item.name for item in path.parent.iterdir()) == ["demo", "rules.toml"]
    assert (path.parent / "rules.toml").read_bytes() == RULES
    assert leftovers(root) == ["demo-1.2.3"]
    assert not (tmp_path / "escape").exists()
    assert install("demo", tool, root, opener=refuse) == path


def test_a_stale_directory_is_replaced(tmp_path: Path) -> None:
    tool = make_tool(BINARY, Packaging.BINARY, extras=False)
    stale = tmp_path / "demo-1.2.3"
    stale.mkdir()
    (stale / "demo").write_bytes(b"old")
    (stale / "junk").write_bytes(b"old")
    path = install("demo", tool, tmp_path, opener=opener_for(BINARY))
    assert sorted(item.name for item in path.parent.iterdir()) == ["demo"]
    assert path.read_bytes() == BINARY
    assert leftovers(tmp_path) == ["demo-1.2.3"]


def install_error(tool: Tool, root: Path, opener: Callable[[str], IO[bytes]]) -> str:
    with pytest.raises(ToolchainError) as raised:
        install("demo", tool, root, opener=opener)
    diagnostic = raised.value.diagnostic
    assert diagnostic.rule == "tools.install-failed"
    assert leftovers(root) == []
    return diagnostic.message


def test_a_checksum_mismatch_installs_nothing(tmp_path: Path) -> None:
    tool = make_tool(BINARY, Packaging.BINARY, checksum="0" * 64)
    message = install_error(tool, tmp_path, opener_for(BINARY))
    assert message == (
        f"cannot install demo 1.2.3: checksum mismatch: expected {'0' * 64}, got {sha(BINARY)}"
    )


def test_an_oversized_download_is_abandoned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(INSTALLER, "DOWNLOAD_LIMIT", 8)
    monkeypatch.setattr(INSTALLER, "CHUNK", 4)
    message = install_error(make_tool(BINARY, Packaging.BINARY), tmp_path, opener_for(BINARY))
    assert message == "cannot install demo 1.2.3: download exceeds 8 bytes"


@pytest.mark.parametrize(
    ("download", "packaging", "member", "fragment"),
    [
        (tar_bytes({}, directories=("pkg/demo",)), Packaging.TAR, "pkg/demo", "not a regular file"),
        (tar_bytes({}), Packaging.TAR, "pkg/link", "not a regular file"),
        (tar_bytes({"pkg/other": BINARY}), Packaging.TAR, "pkg/demo", "pkg/demo"),
        (zip_bytes({"pkg/other": BINARY}), Packaging.ZIP, "pkg/demo", "pkg/demo"),
        (b"not an archive", Packaging.ZIP, "pkg/demo", "not a zip file"),
        (b"not an archive", Packaging.TAR, "pkg/demo", "cannot install demo 1.2.3"),
    ],
    ids=["tar-directory", "tar-symlink", "tar-missing", "zip-missing", "bad-zip", "bad-tar"],
)
def test_unusable_archives_install_nothing(
    tmp_path: Path, download: bytes, packaging: Packaging, member: str, fragment: str
) -> None:
    tool = make_tool(download, packaging, member)
    assert fragment in install_error(tool, tmp_path, opener_for(download))


def test_a_failed_extra_installs_nothing(tmp_path: Path) -> None:
    def opener(url: str) -> IO[bytes]:
        if url.endswith("/rules"):
            msg = "connection reset"
            raise OSError(msg)
        return io.BytesIO(BINARY)

    message = install_error(make_tool(BINARY, Packaging.BINARY), tmp_path, opener)
    assert message == "cannot install demo 1.2.3: connection reset"


@pytest.mark.parametrize("name", ["riscv64", "aarch64"])
def test_a_machine_without_an_artifact_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setattr(platform, "machine", lambda: name)
    tool = make_tool(BINARY, Packaging.BINARY)
    x86 = {Architecture.X86_64: tool.artifacts[Architecture.X86_64]}
    tool = tool.model_copy(update={"artifacts": x86})
    message = install_error(tool, tmp_path / "cache", refuse)
    assert message == f"demo 1.2.3 has no artifact for {name}"
