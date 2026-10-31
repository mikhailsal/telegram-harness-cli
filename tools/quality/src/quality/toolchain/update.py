"""`quality tools update`: move a pinned tool to another release (decision 0016).

The new artifact URLs and archive members are the old ones with the version
replaced. Their checksums come from the vendor's checksum file that the tool's
`checksums` template names, the same source as the original pins; extras,
which have no vendor checksum, are downloaded and hashed. The manifest text is
rewritten in place, so comments and order stay, and the result must parse to
the old manifest with only that tool changed.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from contextlib import closing
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import IO

from quality.diagnostics import Diagnostic, Kind, Rule
from quality.toolchain.install import CHUNK, Opener, https_opener
from quality.toolchain.manifest import (
    TOOL_VERSION,
    Artifact,
    Extra,
    ManifestError,
    Tool,
    parse_manifest,
)

UPDATE_INVALID = Rule(
    id="tools.update-invalid",
    title="A pinned tool cannot be moved to the requested version",
    requirement="R10",
    explanation=(
        "`quality tools update` derives the new pins from the old ones: every artifact URL and "
        "extra URL must contain the old version, the tool must name the vendor's checksum file "
        "in `checksums`, and the rewritten manifest must differ from the old one only in that "
        "tool."
    ),
    remedy="Edit the tool in tools/quality/toolchains.toml by hand, with the vendor's checksums.",
    kind=Kind.USAGE,
)
UPDATE_FAILED = Rule(
    id="tools.update-failed",
    title="The checksums of the new release could not be fetched",
    requirement="R10",
    explanation=(
        "The vendor's checksum file or an extra file could not be downloaded, or the checksum "
        "file does not list one of the new artifacts. Nothing was written."
    ),
    remedy="Check the version, the network, and the tool's `checksums` template, then retry.",
    kind=Kind.ENVIRONMENT,
)
RULES = (UPDATE_INVALID, UPDATE_FAILED)
DOCUMENT_LIMIT = 16 * 1024 * 1024
LISTED = re.compile(r"([0-9a-f]{64})\s+\*?(\S+)")
VALUE = re.compile(r'^(\s*(version|url|sha256|member)\s*=\s*)"([^"]*)"')
HEADER = re.compile(r"^\s*\[\[?\s*(.+?)\s*\]\]?\s*(?:#.*)?$")


class UpdateError(Exception):
    """Why a tool could not be moved to another release; nothing was written."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class Updated:
    """The tool before and after, and the rewritten manifest text."""

    old: Tool
    new: Tool
    text: str


def _invalid(message: str) -> UpdateError:
    return UpdateError(UPDATE_INVALID.diagnostic(message))


def _failed(message: str) -> UpdateError:
    return UpdateError(UPDATE_FAILED.diagnostic(message))


def _drain(response: IO[bytes], url: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := response.read(CHUNK):
        total += len(chunk)
        if total > DOCUMENT_LIMIT:
            msg = f"{url} exceeds {DOCUMENT_LIMIT} bytes"
            raise _failed(msg)
        chunks.append(chunk)
    return b"".join(chunks)


def _read(url: str, opener: Opener) -> bytes:
    try:
        with closing(opener(url)) as response:
            return _drain(response, url)
    except (ManifestError, OSError) as error:
        msg = f"cannot download {url}: {error}"
        raise _failed(msg) from error


def _listed(document: bytes) -> dict[str, str]:
    lines = document.decode("utf-8", errors="replace").splitlines()
    matches = (LISTED.fullmatch(line.strip()) for line in lines)
    return {match[2]: match[1] for match in matches if match}


class _Checksums:
    """The vendor checksum files of one release, each fetched once."""

    def __init__(self, template: str, version: str, opener: Opener) -> None:
        self.template = template.replace("{version}", version)
        self.opener = opener
        self.documents: dict[str, dict[str, str]] = {}

    def of(self, url: str) -> str:
        location = self.template.replace("{url}", url)
        if location not in self.documents:
            self.documents[location] = _listed(_read(location, self.opener))
        name = PurePosixPath(urllib.parse.urlsplit(url).path).name
        if (sha := self.documents[location].get(name)) is None:
            msg = f"{location} does not list {name}"
            raise _failed(msg)
        return sha


def _renamed(value: str, old: str, new: str, what: str) -> str:
    if old not in value:
        msg = f"{what} does not contain the version {old}: {value}"
        raise _invalid(msg)
    return value.replace(old, new)


def _artifact(artifact: Artifact, url: str, sums: _Checksums, old: str, new: str) -> Artifact:
    member = artifact.member.replace(old, new) if artifact.member else None
    return artifact.model_copy(update={"url": url, "sha256": sums.of(url), "member": member})


def _next_tool(tool: Tool, version: str, opener: Opener) -> Tool:
    if tool.checksums is None:
        msg = "the tool names no vendor checksum file in 'checksums'"
        raise _invalid(msg)
    sums = _Checksums(tool.checksums, version, opener)
    old = tool.version
    urls = {
        arch: _renamed(artifact.url, old, version, f"the {arch} URL")
        for arch, artifact in tool.artifacts.items()
    }
    extras = {
        name: _renamed(extra.url, old, version, f"the URL of the extra '{name}'")
        for name, extra in tool.extras.items()
    }
    artifacts = {
        arch: _artifact(artifact, urls[arch], sums, old, version)
        for arch, artifact in tool.artifacts.items()
    }
    hashed = {
        name: Extra(url=url, sha256=hashlib.sha256(_read(url, opener)).hexdigest())
        for name, url in extras.items()
    }
    return tool.model_copy(update={"version": version, "artifacts": artifacts, "extras": hashed})


def _changes(old: Tool, new: Tool) -> dict[tuple[str, str], str]:
    changes = {("version", old.version): new.version}
    for arch, artifact in old.artifacts.items():
        moved = new.artifacts[arch]
        changes["url", artifact.url] = moved.url
        changes["sha256", artifact.sha256] = moved.sha256
        if artifact.member and moved.member:
            changes["member", artifact.member] = moved.member
    for name, extra in old.extras.items():
        changes["url", extra.url] = new.extras[name].url
        changes["sha256", extra.sha256] = new.extras[name].sha256
    return changes


def _rewrite(text: str, name: str, changes: dict[tuple[str, str], str]) -> str:
    own = f"tools.{name}"
    inside = False
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        if header := HEADER.match(line):
            inside = header[1] == own or header[1].startswith(f"{own}.")
        match = VALUE.match(line) if inside else None
        replacement = changes.get((match[2], match[3])) if match else None
        if match and replacement is not None:
            lines.append(match[1] + json.dumps(replacement) + line[match.end() :])
        else:
            lines.append(line)
    return "".join(lines)


def update(text: str, name: str, version: str, *, opener: Opener = https_opener) -> Updated:
    """Return the manifest `text` with the tool `name` moved to `version`."""
    try:
        manifest = parse_manifest(text.encode("utf-8"))
    except ManifestError as error:
        raise _invalid(str(error)) from error
    if (tool := manifest.tools.get(name)) is None:
        msg = f"unknown tool '{name}'; the manifest pins {', '.join(manifest.tools)}"
        raise _invalid(msg)
    if not re.fullmatch(TOOL_VERSION, version) or version == tool.version:
        msg = f"'{version}' is not a release other than the pinned {tool.version}"
        raise _invalid(msg)
    new = _next_tool(tool, version, opener)
    rewritten = _rewrite(text, name, _changes(tool, new))
    expected = manifest.model_copy(update={"tools": {**manifest.tools, name: new}})
    # Every new value matches the manifest's patterns, so the rewritten text parses.
    if parse_manifest(rewritten.encode("utf-8")) != expected:
        msg = f"the rewritten manifest would change more than {name}; update it by hand"
        raise _invalid(msg)
    return Updated(tool, new, rewritten)
