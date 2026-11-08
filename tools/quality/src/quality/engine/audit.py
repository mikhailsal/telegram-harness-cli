"""`quality audit`: known vulnerabilities in the candidate's lockfiles (R04, decision 0015).

The audit belongs to merge CI, not to the local gate: fresh advisory data needs
the network. It scans every lockfile classified as configuration with the
pinned osv-scanner. An advisory whose highest CVSS score reaches
`[audit].blocking_score` is an error, a lower one a warning, and one without a
score follows `[audit].unscored`. A scanner or database failure is an
environment error, never a clean result. Exceptions apply as in `check`.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from quality.adapters.native import ToolFailedError
from quality.adapters.osv import Advisory, scan_lockfiles
from quality.diagnostics import Diagnostic, Kind, Location, Rule, Severity
from quality.discovery import Inventory, build_inventory
from quality.engine.scope import checked_policy
from quality.gitio import Candidate, Repository, TreeEntry, materialize
from quality.policy import AuditPolicy, Category
from quality.rules import exceptions

VULNERABLE = Rule(
    id="dependencies.vulnerable",
    title="A locked dependency has a known vulnerability",
    requirement="R04",
    explanation=(
        "osv-scanner matched a package version in a lockfile against the OSV database. "
        "Advisories scored at or above `[audit].blocking_score` (CVSS) are errors, lower "
        "scores are warnings, and unscored advisories follow `[audit].unscored`. The symbol "
        "is the first advisory ID, for an exception that names exactly one advisory."
    ),
    remedy="Update the package to a fixed version and lock again, or add a dated exception.",
)
SCANNER = Rule(
    id="dependencies.scanner",
    title="The vulnerability scan could not run",
    requirement="R04",
    explanation=(
        "The scanner failed or its advisory database was missing, unreadable, or could not "
        "be downloaded. Without a scan there is no result, so the audit fails."
    ),
    remedy="Fix the network or the database directory named in the message and run again.",
    kind=Kind.ENVIRONMENT,
)
RULES = (VULNERABLE, SCANNER)

LOCKFILES = frozenset({"uv.lock", "package-lock.json", "pubspec.lock", "composer.lock"})


@dataclass(frozen=True, slots=True)
class AuditSettings:
    """The pinned scanner and where advisories come from: a database directory or osv.dev."""

    scanner: Path
    rules: Mapping[str, Rule]
    today: date
    database: Path | None = None


@dataclass(frozen=True, slots=True)
class AuditReport:
    """The findings of one audit and the lockfiles it scanned."""

    diagnostics: tuple[Diagnostic, ...]
    lockfiles: tuple[str, ...] = ()
    excepted: int = 0


def lockfiles(entries: tuple[TreeEntry, ...], inventory: Inventory) -> list[str]:
    """Return the regular lockfiles that the inventory classifies as configuration."""
    config = {r.path for r in inventory.files if r.category is Category.CONFIG}
    return [
        entry.path
        for entry in entries
        if entry.is_file and PurePosixPath(entry.path).name in LOCKFILES and entry.path in config
    ]


def severity(advisory: Advisory, policy: AuditPolicy) -> Severity:
    """Return how an advisory counts under the audit policy."""
    if advisory.score is None:
        return Severity.ERROR if policy.unscored == "error" else Severity.WARNING
    return Severity.ERROR if advisory.score >= policy.blocking_score else Severity.WARNING


def vulnerable(path: str, advisory: Advisory, policy: AuditPolicy) -> Diagnostic:
    """Report one advisory at the lockfile that locks the affected version."""
    names = ", ".join(dict.fromkeys((*advisory.ids, *advisory.aliases)))
    scored = "no CVSS score" if advisory.score is None else f"CVSS {advisory.score:.1f}"
    message = (
        f"{advisory.package} {advisory.version} ({advisory.ecosystem}) is affected by "
        f"{names} ({scored})"
    )
    return VULNERABLE.diagnostic(
        message,
        location=Location(path),
        severity=severity(advisory, policy),
        data={"symbol": advisory.ids[0]},
    )


def _scan(
    root: Path, work: Path, paths: list[str], settings: AuditSettings, policy: AuditPolicy
) -> list[Diagnostic]:
    scan = Path(tempfile.mkdtemp(prefix="osv-", dir=work))
    try:
        found = scan_lockfiles(
            [root / path for path in paths],
            scan,
            scanner=settings.scanner,
            database=settings.database,
        )
    except ToolFailedError as error:
        return [SCANNER.diagnostic(error.diagnostic.message)]
    return [
        vulnerable(path, advisory, policy)
        for index, path in enumerate(paths)
        for advisory in found.get(index, [])
    ]


def audit_candidate(repo: Repository, candidate: Candidate, settings: AuditSettings) -> AuditReport:
    """Scan the candidate's lockfiles and apply the audit policy and exceptions."""
    with materialize(repo, candidate.tree) as snapshot:
        policy = checked_policy(snapshot.root, settings.rules)
        if isinstance(policy, tuple):
            return AuditReport(policy)
        inventory = build_inventory(policy, snapshot.root, snapshot.entries)
        paths = lockfiles(snapshot.entries, inventory)
        found = _scan(snapshot.root, snapshot.work, paths, settings, policy.audit)
        applied = exceptions.apply_exceptions(
            found,
            policy.exceptions,
            rules=settings.rules,
            ran=exceptions.Ran(frozenset(rule.id for rule in RULES)),
            today=settings.today,
        )
    candidate.verify_unchanged()
    return AuditReport(applied.diagnostics, tuple(paths), applied.excepted)
