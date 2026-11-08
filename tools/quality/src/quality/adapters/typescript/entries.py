"""The HTML entry points of a browser component (decision 0017).

Each declared entry point must be a file of the candidate, and every script,
stylesheet, icon, manifest, and preload it references must resolve to a file
the component owns, the way Vite resolves it: a root-relative URL from the
component root or its `public` directory, any other URL from the entry's own
directory. A reference to another origin fails, because the production build
and the browser scenarios load only the candidate; a `data:` URL is inline.
Vite replaces `%VITE_NAME%` with a public value, so only declared names count.
The page is read as data: nothing in it runs.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from quality.boundedio import ReportError, read_bounded
from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import Component

ENTRIES = Rule(
    id="typescript.entries",
    title="HTML entry point is missing or references a file that does not exist",
    requirement="R04",
    explanation=(
        "Every `entries` page of a browser component must be in the candidate, and its "
        "scripts, stylesheets, icons, manifests, and preloads must resolve to files of the "
        "component: `/path` from the component root or `public/`, other paths from the page's "
        "directory. Other origins and undeclared `%VITE_*%` values fail."
    ),
    remedy=(
        "Add the missing file, correct the URL, or declare the public value in "
        "`[components.browser] public_env`."
    ),
)
RULES = (ENTRIES,)

LINKED = frozenset(
    {"stylesheet", "icon", "apple-touch-icon", "mask-icon", "manifest"}
    | {"modulepreload", "preload", "prefetch"}
)
PUBLIC = "public"
VALUE = re.compile(r"%(VITE_[A-Z0-9_]+)%")
PAGE_LIMIT = 4 * 1024 * 1024


class References(HTMLParser):
    """The script and link URLs of one page with their lines."""

    def __init__(self) -> None:
        """Start with no URLs; character references in attributes are decoded."""
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record the `src` of a script and the `href` of a link that loads a resource."""
        values = {name: value or "" for name, value in attrs}
        if tag == "script" and "src" in values:
            self.found.append((self.getpos()[0], values["src"]))
        elif tag == "link" and LINKED & set(values.get("rel", "").lower().split()):
            self.found.append((self.getpos()[0], values.get("href", "")))


@dataclass(frozen=True, slots=True)
class Page:
    """One entry point of a component and the files the component owns."""

    component: Component
    path: str
    owned: frozenset[str]

    def candidates(self, url: str) -> list[str]:
        """Return the repository paths a local URL can name."""
        if url.startswith("/"):
            roots = (self.component.root, posixpath.join(self.component.root, PUBLIC))
            return [posixpath.normpath(posixpath.join(root, url[1:])) for root in roots]
        return [posixpath.normpath(posixpath.join(posixpath.dirname(self.path), url))]

    def problem(self, reference: str) -> str | None:
        """Return why a reference of the page does not resolve, or None."""
        declared = self.component.browser_settings.public_env
        text = VALUE.sub(lambda match: declared.get(match.group(1), match.group(0)), reference)
        text = text.strip()
        undeclared = VALUE.search(text)
        if undeclared is not None:
            return f"'{reference}' names {undeclared.group(1)}, which public_env does not declare"
        parts = urlsplit(text)
        if parts.scheme == "data":
            return None
        if parts.scheme or parts.netloc:
            return f"'{text}' loads from another origin; the page may load only its own files"
        url = unquote(parts.path)
        if url and any(path in self.owned for path in self.candidates(url)):
            return None
        return f"'{text}' does not resolve to a file of the component"


def _references(snapshot: Path, path: str) -> list[tuple[int, str]] | str:
    try:
        data = read_bounded(snapshot / path, PAGE_LIMIT)
    except ReportError:
        return f"the entry point is not a regular file of at most {PAGE_LIMIT >> 20} MiB"
    parser = References()
    parser.feed(data.decode("utf-8", errors="replace"))
    parser.close()
    return parser.found


def check_entries(
    component: Component, snapshot: Path, entries: tuple[str, ...], owned: frozenset[str]
) -> list[Diagnostic]:
    """Report missing entry points and references that do not resolve."""
    found: list[Diagnostic] = []
    for path in entries:
        if path not in owned:
            message = f"the declared entry point {path} is not a file of the candidate"
            found.append(ENTRIES.diagnostic(message, component=component.id))
            continue
        references = _references(snapshot, path)
        if isinstance(references, str):
            found.append(
                ENTRIES.diagnostic(references, location=Location(path), component=component.id)
            )
            continue
        page = Page(component, path, owned)
        for line, reference in references:
            message = page.problem(reference)
            if message is not None:
                found.append(
                    ENTRIES.diagnostic(
                        message, location=Location(path, line), component=component.id
                    )
                )
    return found
