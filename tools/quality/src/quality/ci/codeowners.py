"""CODEOWNERS parsing with GitHub's matching: the last matching pattern decides the owners.

GitHub follows gitignore patterns with exceptions: `!` negation and `[ ]`
ranges are unsupported, and a wildcard in the last segment matches files at
that level only (`docs/*` does not own `docs/a/b.md`). Where the semantics
could differ, this parser matches less, so a file is never counted as owned
when GitHub would not require its owners' review.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from quality.diagnostics import Diagnostic, Location, Rule

CODEOWNERS = Rule(
    id="ci.codeowners",
    title="A control file has no code owner",
    requirement="R09",
    explanation=(
        "Control files decide what the gate checks: the policy, the core and hooks, CI "
        "entry points, native configuration, scenario manifests, and CODEOWNERS itself. "
        "Each needs an owner in the CODEOWNERS file named in `[ci]`, so a hosting rule "
        "that requires owner review covers every change to them. GitHub reads the first "
        "of .github/CODEOWNERS, CODEOWNERS, and docs/CODEOWNERS; lines it would ignore "
        "are reported."
    ),
    remedy="Add an owner for the file, or fix the reported CODEOWNERS line.",
)
RULES = (CODEOWNERS,)

LOCATIONS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
OWNER = re.compile(r"@[A-Za-z0-9][A-Za-z0-9-]*(?:/[A-Za-z0-9][\w.-]*)?|[^@\s]+@[^@\s]+\.[^@\s]+")
UNSUPPORTED = re.compile(r"^!|\[|\]")


@dataclass(frozen=True, slots=True)
class Entry:
    """One pattern line: what it matches and who owns it; no owners means unowned."""

    line: int
    pattern: str
    owners: tuple[str, ...]
    regex: re.Pattern[str]


def _segment(segment: str) -> str:
    return "".join(
        "[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char) for char in segment
    )


def pattern_regex(pattern: str) -> re.Pattern[str]:
    """Translate a CODEOWNERS pattern to a regular expression over repository paths."""
    directory = pattern.endswith("/")
    body = pattern.rstrip("/")
    anchored = "/" in body
    parts = body.removeprefix("/").split("/")
    out: list[str] = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            out.append(".*" if last else "(?:.*/)?")
        else:
            out.append(_segment(part) + ("" if last else "/"))
    literal_end = not any(char in parts[-1] for char in "*?")
    if directory:
        tail = "/.*"
    elif literal_end:
        tail = "(?:/.*)?"
    else:
        tail = ""
    return re.compile(("" if anchored else "(?:.*/)?") + "".join(out) + tail)


def _problem(pattern: str, owners: list[str]) -> str | None:
    if UNSUPPORTED.search(pattern):
        return f"pattern '{pattern}' uses syntax GitHub does not support"
    invalid = [owner for owner in owners if OWNER.fullmatch(owner) is None]
    if invalid:
        return f"'{invalid[0]}' is not a user, team, or email address"
    return None


def parse_codeowners(path: str, text: str) -> tuple[list[Entry], list[Diagnostic]]:
    """Return the usable entries and a finding for each line GitHub would not use."""
    entries: list[Entry] = []
    problems: list[Diagnostic] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        pattern, *owners = line.split()
        problem = _problem(pattern, owners)
        if problem is not None:
            where = Location(path, number)
            problems.append(CODEOWNERS.diagnostic(problem, location=where, data={"symbol": line}))
            continue
        literal = pattern.replace("\\#", "#")
        entries.append(Entry(number, pattern, tuple(owners), pattern_regex(literal)))
    return entries, problems


def owners_of(entries: Iterable[Entry], path: str) -> tuple[str, ...]:
    """Return the owners of a path: those of the last matching entry, if any."""
    owners: tuple[str, ...] = ()
    for entry in entries:
        if entry.regex.fullmatch(path):
            owners = entry.owners
    return owners


def unowned(path: str, entries: list[Entry], control: Iterable[str]) -> Iterator[Diagnostic]:
    """Report each control file that no CODEOWNERS entry gives an owner."""
    for file in control:
        if not owners_of(entries, file):
            message = f"control file '{file}' has no owner in {path}"
            yield CODEOWNERS.diagnostic(message, location=Location(file), data={"symbol": path})


def shadowing(configured: str, present: Iterable[str]) -> Iterator[Diagnostic]:
    """Report a CODEOWNERS file that GitHub reads instead of the configured one."""
    existing = set(present)
    if configured not in existing:
        message = f"the CODEOWNERS file '{configured}' does not exist"
        yield CODEOWNERS.diagnostic(message, location=Location(configured))
        return
    if configured not in LOCATIONS:
        message = f"GitHub does not read CODEOWNERS from '{configured}'"
        yield CODEOWNERS.diagnostic(message, location=Location(configured))
        return
    for location in LOCATIONS[: LOCATIONS.index(configured)]:
        if location in existing:
            message = f"GitHub reads '{location}' before the configured '{configured}'"
            yield CODEOWNERS.diagnostic(message, location=Location(location))
