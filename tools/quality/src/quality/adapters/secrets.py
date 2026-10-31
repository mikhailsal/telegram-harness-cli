"""Secret scanning with the pinned gitleaks rule set and no candidate-controlled settings."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from quality.adapters.native import fail, run_tool
from quality.boundedio import JsonValue, ReportError, load_json
from quality.diagnostics import Diagnostic, Location, Rule
from quality.process import Command, sanitized

DETECTED = Rule(
    id="secrets.detected",
    title="Secret or private key material detected",
    requirement="R04",
    explanation=(
        "The candidate tree and every blob added in new history are scanned with the pinned "
        "gitleaks rules. The rules' global path allowlist is removed, so lockfiles, vendored "
        "code, generated files, and images are scanned too; archives are opened; inline "
        "allow comments are ignored. Findings are redacted."
    ),
    remedy=(
        "Remove the secret from the change and from history, then revoke it. Build test "
        "credentials at run time instead of committing them."
    ),
)
RULES = (DETECTED,)

LEAKS = 9
TIMEOUT = 600.0
ARCHIVE_DEPTH = "3"
HEADER = re.compile(r"^\[allowlist\]\s*$", re.MULTILINE)
PATHS = re.compile(r"^paths\s*=\s*\[.*?^\]\s*\n", re.MULTILINE | re.DOTALL)
TABLE = re.compile(r"^\[", re.MULTILINE)


class RulesError(Exception):
    """The upstream rule set does not have the expected shape."""


def derive_config(upstream: str) -> str:
    """Remove only the global path allowlist from the upstream rule set.

    The result is parsed again and must equal the upstream document without
    `allowlist.paths`, so no rule or other allowlist can change by accident.
    """
    header = HEADER.search(upstream)
    if header is None:
        msg = "the rule set has no global [allowlist] table"
        raise RulesError(msg)
    paths = PATHS.search(upstream, header.end())
    following = TABLE.search(upstream, header.end())
    if paths is None or (following is not None and following.start() < paths.start()):
        msg = "the global allowlist has no 'paths' array"
        raise RulesError(msg)
    derived = upstream[: paths.start()] + upstream[paths.end() :]
    expected = tomllib.loads(upstream)
    expected["allowlist"].pop("paths")
    if tomllib.loads(derived) != expected:
        msg = "removing the path allowlist changed other settings"
        raise RulesError(msg)
    return derived


@dataclass(frozen=True, slots=True)
class Finding:
    """One redacted finding, relative to the scanned directory."""

    path: str
    line: int
    column: int
    rule: str
    description: str


def _text(entry: dict[str, JsonValue], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        fail("gitleaks", f"report entry without text field {key}")
    return value


def _number(entry: dict[str, JsonValue], key: str) -> int:
    value = entry.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        fail("gitleaks", f"report entry without number field {key}")
    return value


def _findings(report: JsonValue, root: Path) -> Iterator[Finding]:
    if not isinstance(report, list):
        fail("gitleaks", "report is not a list")
    for entry in report:
        if not isinstance(entry, dict):
            fail("gitleaks", "report entry is not an object")
        absolute = PurePosixPath(_text(entry, "File"))
        if not absolute.is_relative_to(root.as_posix()):
            fail("gitleaks", f"finding outside the scanned tree: {absolute}")
        yield Finding(
            path=absolute.relative_to(root.as_posix()).as_posix(),
            line=_number(entry, "StartLine"),
            column=_number(entry, "StartColumn"),
            rule=_text(entry, "RuleID"),
            description=_text(entry, "Description"),
        )


def scan_directory(root: Path, work: Path, *, gitleaks: Path, rules: Path) -> list[Finding]:
    """Scan a materialized tree; the working directory holds only files the core wrote."""
    try:
        upstream = rules.read_text(encoding="utf-8")
        config = derive_config(upstream)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, RulesError) as error:
        fail("gitleaks", f"cannot prepare rules from {rules}: {error}")
    directory = work / "gitleaks"
    directory.mkdir()
    (directory / "gitleaks.toml").write_text(config, encoding="utf-8")
    report = directory / "report.json"
    argv = (str(gitleaks), "dir", str(root), "--config", str(directory / "gitleaks.toml"))
    argv += ("--gitleaks-ignore-path", str(directory), "--max-archive-depth", ARCHIVE_DEPTH)
    argv += ("--report-format", "json", "--report-path", str(report), "--redact")
    # Inline allow comments would let a secret pass although secret findings cannot be excepted.
    argv += ("--ignore-gitleaks-allow",)
    argv += ("--no-banner", "--no-color", "--log-level", "error", "--exit-code", str(LEAKS))
    command = Command(argv, cwd=directory, timeout=TIMEOUT, env=sanitized())
    completed = run_tool(command, name="gitleaks", accepted={0, LEAKS})
    try:
        found = list(_findings(load_json(report), root))
    except ReportError as error:
        fail("gitleaks", str(error))
    if (completed.returncode == LEAKS) != bool(found):
        fail("gitleaks", "exit status and report disagree")
    return found


type Relabel = Callable[[str], tuple[str, str]]


def _same(path: str) -> tuple[str, str]:
    return path, ""


def secret_diagnostics(found: list[Finding], relabel: Relabel = _same) -> Iterator[Diagnostic]:
    """Report findings without secret content; `relabel` maps scanned paths for history."""
    for finding in found:
        path, origin = relabel(finding.path)
        yield DETECTED.diagnostic(
            f"{origin}{finding.rule}: {finding.description} (redacted)",
            location=Location(path, finding.line, finding.column),
        )
