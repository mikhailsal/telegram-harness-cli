"""Builders for classified candidate files and the pinned tools used by gate tests."""

from __future__ import annotations

import sys
from pathlib import Path

from quality.discovery import FileRecord, Region
from quality.engine import Tools
from quality.policy import Category
from quality.rules.model import CandidateFile
from quality.toolchain import cache_root, extra_path, load_manifest, locate

REGIONS = {Category.SOURCE: Region.SOURCE, Category.TESTS: Region.TESTS}


def candidate(
    path: str,
    data: bytes | str | None,
    category: Category = Category.SOURCE,
    *,
    language: str | None = None,
    component: str | None = "app",
) -> CandidateFile:
    """Return a candidate file; `data` None models a symlink or an oversized blob."""
    content = data.encode() if isinstance(data, str) else data
    region = REGIONS.get(category, Region.OTHER)
    record = FileRecord(path, category, component, language, region)
    return CandidateFile(record, len(content or b""), content)


def pinned_tools() -> Tools:
    """Return the pinned tools from the local cache; fail loudly when they are missing."""
    tool = load_manifest().tools["gitleaks"]
    root = cache_root()
    rules = extra_path("gitleaks", tool, root, "gitleaks.toml")
    gitleaks = locate("gitleaks", tool, root)
    if not rules.is_file():
        msg = f"run `quality tools install` first; {rules} is missing"
        raise AssertionError(msg)
    actionlint = locate("actionlint", load_manifest().tools["actionlint"], root)
    return Tools(Path(sys.executable), gitleaks, rules, actionlint)
