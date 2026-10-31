"""The output of a browser component's production build (decision 0018).

The evidence of a build names the candidate, every output file with its
SHA-256, and their combined digest: the SHA-256 of the `sha256sum` listing of
the files sorted by path, so the digest can be recomputed with standard tools.
The built entry points are validated against that output, never against the
source: each script and linked resource of a declared entry point, which Vite
writes at the same path below the output, and each `url()` of a built
stylesheet must name a file of the output. A URL that starts with an absolute
`base` is read from the output root, another from the referring file's
directory; a `data:` URL and a fragment are inline, and another origin fails,
because the scenarios load only the candidate's files.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from quality.adapters.native import failure
from quality.adapters.typescript.entries import References
from quality.boundedio import JsonValue, ReportError, read_bounded
from quality.diagnostics import Diagnostic, Rule
from quality.policy import Component

OUTPUT = Rule(
    id="typescript.build-output",
    title="The production build references a file it did not write",
    requirement="R04",
    explanation=(
        "Every script, stylesheet, icon, manifest, and preload of a built entry point, and "
        "every `url()` of a built stylesheet, must name a file of the build's output. Vite "
        "leaves an asset it cannot resolve unchanged, and the page would load it from "
        "wherever it is served."
    ),
    remedy="Import the asset from the source, or put it in `public/`, so the build emits it.",
)
RULES = (OUTPUT,)
TOOL = "vite"
URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)'"\s]*))\s*\)""")
READ_LIMIT = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Output:
    """A build's output directory and evidence: `files` maps each path to its SHA-256."""

    directory: Path
    identity: str
    files: dict[str, str]

    @property
    def digest(self) -> str:
        """Return the SHA-256 of the `sha256sum` listing of every output file."""
        listing = "".join(f"{digest}  {path}\n" for path, digest in sorted(self.files.items()))
        return hashlib.sha256(listing.encode()).hexdigest()

    def evidence(self) -> dict[str, JsonValue]:
        """Return the evidence record of the build."""
        files: dict[str, JsonValue] = dict(sorted(self.files.items()))
        return {"candidate": self.identity, "digest": self.digest, "files": files}


def collect(directory: Path, identity: str) -> Output:
    """Hash every regular file below the output directory; a symlink is not an output."""
    files: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                files[path.relative_to(directory).as_posix()] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    return Output(directory, identity, files)


def _text(output: Output, path: str) -> str:
    try:
        data = read_bounded(output.directory / path, READ_LIMIT)
    except ReportError as error:
        raise failure(TOOL, f"the built {path} cannot be read: {error}") from error
    return data.decode("utf-8", errors="replace")


def _urls(output: Output, path: str) -> Iterator[str]:
    text = _text(output, path)
    if not path.endswith(".css"):
        parser = References()
        parser.feed(text)
        parser.close()
        yield from (url for _, url in parser.found)
    else:
        for match in URL.finditer(text):
            yield next(group for group in match.groups() if group is not None)


def problem(output: Output, path: str, reference: str, base: str) -> str | None:
    """Return why one reference of an output file names no file of the output, or None."""
    parts = urlsplit(reference.strip())
    if parts.scheme == "data" or (not parts.path and not parts.netloc):
        return None
    if parts.scheme or parts.netloc:
        return f"'{reference}' loads from another origin"
    url = unquote(parts.path)
    if base.startswith("/") and url.startswith(base):
        target = posixpath.normpath(url[len(base) :])
    else:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(path), url))
    return None if target in output.files else f"'{reference}' is not a file of the build"


def _problems(output: Output, path: str, base: str) -> Iterator[str]:
    for reference in _urls(output, path):
        message = problem(output, path, reference, base)
        if message is not None:
            yield f"{path}: {message}"


def check_output(component: Component, output: Output, base: str) -> list[Diagnostic]:
    """Report missing built entry points and the references the output does not hold."""
    entries = [posixpath.normpath(entry) for entry in component.browser_settings.entries]
    messages = [f"the build wrote no {entry}" for entry in entries if entry not in output.files]
    styles = [path for path in output.files if path.endswith(".css")]
    for path in (*(entry for entry in entries if entry in output.files), *styles):
        messages.extend(_problems(output, path, base))
    return [OUTPUT.diagnostic(message, component=component.id) for message in messages]
