"""The settings only a browser component has, and the rules of its declaration (decision 0017).

A TypeScript component runs on Node unless it declares `target = "browser"`;
then it names its framework and bundler, declares the `ui` capability, and
may refine the defaults below in its `[components.browser]` table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated

from pydantic import AfterValidator, Field, Strict, StringConstraints

from quality.policy.enums import Bundler, Capability, ExecutionTarget, Framework, Profile
from quality.policy.types import Glob, Identifier, RelPath, StrictModel

_PUBLIC_NAME = re.compile(r"^VITE_[A-Z0-9_]+$")
# npm package names, optionally scoped: lowercase, no leading dot or underscore.
_PACKAGE = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")


def _check_public_name(value: str) -> str:
    if not _PUBLIC_NAME.fullmatch(value):
        msg = "must be an uppercase name with the 'VITE_' prefix that Vite exposes to the client"
        raise ValueError(msg)
    return value


def _check_package(value: str) -> str:
    if not _PACKAGE.fullmatch(value):
        msg = "must be an npm package name such as 'pg' or '@scope/name'"
        raise ValueError(msg)
    return value


def _check_entry(value: str) -> str:
    if not value.endswith(".html"):
        msg = "must be an HTML file"
        raise ValueError(msg)
    return value


PublicName = Annotated[str, Strict(), AfterValidator(_check_public_name)]
PublicValue = Annotated[
    str, Strict(), StringConstraints(max_length=1024, pattern=r"^[^\r\n\x00]*$")
]
PackageName = Annotated[str, Strict(), AfterValidator(_check_package)]
Entry = Annotated[RelPath, AfterValidator(_check_entry)]
AxeTag = Annotated[
    str, Strict(), StringConstraints(max_length=64, pattern=r"^[a-z0-9][a-z0-9.-]*$")
]
Width = Annotated[int, Strict(), Field(ge=240, le=7680)]
Height = Annotated[int, Strict(), Field(ge=240, le=4320)]


class Viewport(StrictModel):
    """The size of the page in one browser project."""

    width: Width
    height: Height


def _default_viewports() -> dict[str, Viewport]:
    return {"narrow": Viewport(width=390, height=844), "wide": Viewport(width=1280, height=800)}


def _default_tags() -> list[str]:
    return ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]


class BrowserSettings(StrictModel):
    """HTML entry points, public build values, server-only modules, viewports, and scans.

    `entries` are relative to the component root. `public_env` holds the only
    values the production build exposes to the client; they are not secret.
    Browser source may import neither `server_modules` (globs relative to the
    root) nor `server_packages`. Every browser scenario runs in each viewport,
    and every accessibility scan runs at least the axe `accessibility_tags`.
    """

    entries: list[Entry] = Field(default_factory=lambda: ["index.html"], min_length=1)
    public_env: dict[PublicName, PublicValue] = Field(default_factory=dict[str, str])
    server_modules: list[Glob] = Field(default_factory=list[str])
    server_packages: list[PackageName] = Field(default_factory=list[str])
    viewports: dict[Identifier, Viewport] = Field(default_factory=_default_viewports, min_length=1)
    accessibility_tags: list[AxeTag] = Field(default_factory=_default_tags, min_length=1)


@dataclass(frozen=True, slots=True)
class Declaration:
    """The fields of a component that decide where it runs."""

    profile: Profile
    target: ExecutionTarget | None
    framework: Framework | None
    bundler: Bundler | None
    browser: BrowserSettings | None
    capabilities: tuple[Capability, ...]

    def problem(self) -> str | None:
        """Return why the declaration is invalid, or None."""
        if self.target is not None and self.profile is not Profile.TYPESCRIPT:
            return "'target' applies only to components of the typescript profile"
        if self.target is not ExecutionTarget.BROWSER:
            extras = [
                name
                for name, value in (
                    ("framework", self.framework),
                    ("bundler", self.bundler),
                    ("browser", self.browser),
                )
                if value is not None
            ]
            if extras:
                return (
                    f'{", ".join(extras)}: only a component with target = "browser" declares these'
                )
            return None
        if self.framework is None or self.bundler is None:
            return 'a browser component declares framework = "react" and bundler = "vite"'
        if Capability.UI not in self.capabilities:
            return "a browser component declares the 'ui' capability"
        return None
