"""The Git version the gate supports (decision 0002)."""

from __future__ import annotations

import re

from quality.diagnostics import Diagnostic
from quality.gitio.errors import GIT_VERSION
from quality.gitio.repo import Repository

MINIMUM = (2, 34)
PATTERN = re.compile(r"^git version (\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """Return the version `git --version` printed, or None when it is not recognizable."""
    match = PATTERN.match(text.strip())
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def version_findings(repo: Repository) -> tuple[str, list[Diagnostic]]:
    """Return the installed Git version and why it is unsupported, if it is."""
    text = repo.git("--version").stdout_text().strip()
    version = parse_version(text)
    if version is None:
        return text, [GIT_VERSION.diagnostic(f"unrecognized output of 'git --version': {text!r}")]
    shown = ".".join(str(part) for part in version)
    if version[:2] < MINIMUM:
        minimum = ".".join(str(part) for part in MINIMUM)
        return shown, [GIT_VERSION.diagnostic(f"Git {shown} is older than {minimum}")]
    return shown, []
