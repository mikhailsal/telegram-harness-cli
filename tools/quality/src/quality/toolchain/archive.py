"""Place a verified download into a tool's install directory.

A single-file tool is copied out of its archive by the one member the manifest
names, so nothing else in the archive reaches the disk. A tree tool, such as
Node with its bundled npm, is unpacked whole, but only after every member and
every link target is checked to stay below the archive's top directory, and
with the `data` filter, which also rejects devices and special permission
bits. A tree may also be a zip archive, such as the Dart SDK, recognized by
its content: zip has no extraction filter, so every entry is checked the same
way, links are refused outright, and each regular file is written by this
module with mode 0o755 when its Unix mode has any execute bit and 0o644
otherwise. Any violation is a ManifestError, which the installer reports as a
failed installation.
"""

from __future__ import annotations

import os
import posixpath
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path

from quality.toolchain.manifest import Artifact, ManifestError, Packaging, Tool


def copy_member(artifact: Artifact, archive: Path, target: Path) -> None:
    """Copy only the named regular file out of the archive; nothing else is extracted."""
    member = artifact.member or ""
    if artifact.packaging is Packaging.BINARY:
        shutil.copyfile(archive, target)
    elif artifact.packaging is Packaging.TAR:
        with tarfile.open(archive) as tar:
            info = tar.getmember(member)
            stream = tar.extractfile(info) if info.isfile() else None
            if stream is None:
                msg = f"archive member {member} is not a regular file"
                raise ManifestError(msg)
            with stream, target.open("wb") as out:
                shutil.copyfileobj(stream, out)
    else:
        with (
            zipfile.ZipFile(archive) as bundle,
            bundle.open(member) as stream,
            target.open("wb") as out,
        ):
            shutil.copyfileobj(stream, out)
    target.chmod(0o755)


def inside(top: str, name: str) -> bool:
    """Whether a normalized archive path lies below the directory `top`."""
    parts = posixpath.normpath(name).split("/")
    return not posixpath.isabs(name) and len(parts) > 1 and parts[0] == top and ".." not in parts


def check_tree(members: list[tarfile.TarInfo], top: str) -> None:
    """Require every member, and every link target, to stay below the top directory."""
    for info in members:
        name = info.name.removeprefix("./").rstrip("/")
        if name != top and not inside(top, name):
            msg = f"archive member {info.name} is outside {top}/"
            raise ManifestError(msg)
        if info.issym():
            target = posixpath.join(posixpath.dirname(name), info.linkname)
        elif info.islnk():
            target = info.linkname.removeprefix("./")
        else:
            continue
        if posixpath.isabs(info.linkname) or not inside(top, target):
            msg = f"archive link {info.name} points outside {top}/"
            raise ManifestError(msg)


def check_zip(members: list[zipfile.ZipInfo], top: str) -> None:
    """Require every zip entry to stay below the top directory and none to be a link."""
    for info in members:
        name = info.filename.rstrip("/")
        if name != top and not inside(top, name):
            msg = f"archive member {info.filename} is outside {top}/"
            raise ManifestError(msg)
        if stat.S_ISLNK(info.external_attr >> 16):
            msg = f"archive link {info.filename} is not supported in a zip archive"
            raise ManifestError(msg)


def unzip_tree(archive: Path, top: str, unpacked: Path) -> None:
    """Write the checked entries of a zip archive below `unpacked`, never over an existing file."""
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        check_zip(members, top)
        for info in members:
            path = unpacked.joinpath(*posixpath.normpath(info.filename).split("/"))
            if info.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as stream, path.open("xb") as out:
                shutil.copyfileobj(stream, out)
            path.chmod(0o755 if (info.external_attr >> 16) & 0o111 else 0o644)


def unpack_tree(artifact: Artifact, archive: Path, target: Path) -> None:
    """Extract the whole archive below its top directory and move that directory to `target`.

    For tar, the `data` filter also rejects devices and drops setuid and
    group-write bits.
    """
    top = artifact.member or ""
    unpacked = target.parent / "tree"
    if zipfile.is_zipfile(archive):
        unzip_tree(archive, top, unpacked)
    else:
        with tarfile.open(archive, mode="r:*") as tar:
            members = tar.getmembers()
            check_tree(members, top)
            tar.extractall(unpacked, members=members, filter="data")
    if not (unpacked / top).is_dir():
        msg = f"archive has no top directory {top}/"
        raise ManifestError(msg)
    (unpacked / top).rename(target)


def place(artifact: Artifact, download: Path, final: Path, tool: Tool) -> None:
    """Put the tool's files into the new install directory `final`."""
    if artifact.packaging is Packaging.TREE:
        unpack_tree(artifact, download, final)
        program = final / tool.program
        if not (program.is_file() and os.access(program, os.X_OK)):
            msg = f"archive has no executable {tool.program}"
            raise ManifestError(msg)
        return
    final.mkdir()
    copy_member(artifact, download, final / tool.binary)
