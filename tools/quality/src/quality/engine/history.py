"""History-wide checks: every blob a revision range adds, even if deleted before the tip."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from quality.adapters import native, secrets
from quality.diagnostics import Diagnostic
from quality.engine.common import Tools
from quality.gitio import HistoryBlob, Repository, RevisionRange, new_blobs, read_blobs
from quality.rules import files

UNSAFE_PARTS = frozenset({"", ".", ".."})


def _origin(blob_id: str) -> str:
    return f"history blob {blob_id[:12]}: "


def blob_location(blob: HistoryBlob) -> str:
    """Return the relative path a history blob is written to: its ID, then its name."""
    parts = [part for part in PurePosixPath(blob.path).parts if part not in UNSAFE_PARTS]
    name = "/".join(parts) if parts and blob.path != blob.oid else "blob"
    return f"{blob.oid}/{name}"


def _relabel(path: str) -> tuple[str, str]:
    oid, _, rest = path.partition("/")
    return rest, _origin(oid)


def check_history(
    repo: Repository, span: RevisionRange, work: Path, *, tools: Tools, blob_limit: int
) -> list[Diagnostic]:
    """Check the size of and scan for secrets in every blob the range adds."""
    blobs = new_blobs(repo, span)
    found: list[Diagnostic] = []
    for blob in blobs:
        diagnostic = files.check_blob(blob.path, blob.size, blob_limit, origin=_origin(blob.oid))
        if diagnostic is not None:
            found.append(diagnostic)
    tree = work / "history"
    tree.mkdir()
    locations = {blob.oid: blob_location(blob) for blob in blobs}
    for oid, data in read_blobs(repo, [(blob.oid, blob.size) for blob in blobs]):
        target = tree / locations[oid]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    scan_work = work / "history-scan"
    scan_work.mkdir()
    try:
        findings = secrets.scan_directory(
            tree, scan_work, gitleaks=tools.gitleaks, rules=tools.gitleaks_rules
        )
    except native.ToolFailedError as error:
        return [*found, error.diagnostic]
    found.extend(secrets.secret_diagnostics(findings, _relabel))
    return found
