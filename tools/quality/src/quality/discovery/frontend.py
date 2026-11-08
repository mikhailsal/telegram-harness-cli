"""Evidence that a TypeScript project is a React + Vite browser application (decision 0017).

A package.json or a `.tsx` file alone proves nothing: Node programs render JSX
too. A project is a browser application only when its manifest depends on
React, React DOM, Vite, and the React plugin for Vite, a Vite configuration is
at its root, and so is the HTML entry point. Registration requires that
evidence for `target = "browser"`, and reports partial evidence or another
framework, meta-framework, or bundler instead of guessing a target.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from quality import strictjson
from quality.discovery.manifests import MAX_MANIFEST_BYTES, ManifestError

MANIFEST = "package.json"
ENTRY = "index.html"
REQUIRED = ("react", "react-dom", "vite", "@vitejs/plugin-react")
VITE_CONFIGS = tuple(f"vite.config.{ext}" for ext in ("ts", "mts", "cts", "js", "mjs", "cjs"))
DEPENDENCY_TABLES = ("dependencies", "devDependencies", "optionalDependencies")
# Packages that make a project a variant the browser target does not support.
UNSUPPORTED = {
    "next": "Next.js",
    "nuxt": "Nuxt",
    "@remix-run/react": "Remix",
    "@react-router/dev": "React Router framework mode",
    "gatsby": "Gatsby",
    "astro": "Astro",
    "vike": "Vike",
    "react-server-dom-webpack": "React Server Components",
    "react-native": "React Native",
    "expo": "Expo",
    "vue": "Vue",
    "svelte": "Svelte",
    "@sveltejs/kit": "SvelteKit",
    "@angular/core": "Angular",
    "solid-js": "Solid",
    "preact": "Preact",
    "webpack": "webpack",
    "parcel": "Parcel",
    "@rspack/core": "Rspack",
}


@dataclass(frozen=True, slots=True)
class Evidence:
    """What a project's root says about a browser application."""

    packages: frozenset[str]
    vite_config: str | None
    entry: bool
    unsupported: tuple[str, ...]

    @property
    def found(self) -> tuple[str, ...]:
        """Name the browser evidence present; an HTML file alone is none."""
        named = [name for name in REQUIRED if name in self.packages]
        if self.vite_config is not None:
            named.append(self.vite_config)
        if named and self.entry:
            named.append(ENTRY)
        return (*named, *self.unsupported)

    @property
    def missing(self) -> tuple[str, ...]:
        """Name what a React + Vite application lacks."""
        named = [f"the {name} package" for name in REQUIRED if name not in self.packages]
        if self.vite_config is None:
            named.append("a vite.config.* file")
        if not self.entry:
            named.append(f"an {ENTRY} entry point")
        return tuple(named)


def _dependencies(document: object) -> set[str]:
    if not isinstance(document, dict):
        return set()
    tables = (cast(dict[str, object], document).get(name) for name in DEPENDENCY_TABLES)
    return {
        key for table in tables if isinstance(table, dict) for key in cast(dict[str, object], table)
    }


def _names(directory: Path, candidates: Iterable[str]) -> str | None:
    return next((name for name in candidates if (directory / name).is_file()), None)


def inspect(directory: Path) -> Evidence:
    """Collect the browser evidence at a project root; raise ManifestError when unreadable."""
    packages: set[str] = set()
    manifest = directory / MANIFEST
    if manifest.is_file():
        data = manifest.read_bytes()
        if len(data) > MAX_MANIFEST_BYTES:
            msg = f"{MANIFEST} is larger than {MAX_MANIFEST_BYTES} bytes"
            raise ManifestError(msg)
        try:
            packages = _dependencies(strictjson.loads(data.decode("utf-8")))
        except ValueError as error:
            msg = f"{MANIFEST} is not valid JSON: {error}"
            raise ManifestError(msg) from error
    unsupported = tuple(label for name, label in UNSUPPORTED.items() if name in packages)
    return Evidence(
        packages=frozenset(packages & set(REQUIRED)),
        vite_config=_names(directory, VITE_CONFIGS),
        entry=(directory / ENTRY).is_file(),
        unsupported=unsupported,
    )
