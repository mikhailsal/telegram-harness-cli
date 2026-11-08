"""Blob size, source file length, and text encoding rules (R04)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from quality.diagnostics import Diagnostic, Location, Rule, Severity
from quality.policy import Category, Limit, Thresholds
from quality.rules.model import TEXT_CATEGORIES, CandidateFile, physical_lines

BLOB_SIZE = Rule(
    id="files.blob-size",
    title="Tracked file exceeds the blob size limit",
    requirement="R04",
    explanation=(
        "Every tracked blob, including generated, vendored, and asset files and blobs added "
        "and removed again in new history, must stay within thresholds.blob_bytes."
    ),
    remedy="Remove the file from the change, shrink it, or keep it outside the repository.",
)
SOURCE_LINES = Rule(
    id="files.source-lines",
    title="Source file is too long",
    requirement="R04",
    explanation=(
        "Source and test files are counted in physical lines after canonical formatting "
        "(the formatter check must pass too) against thresholds.source_file_lines."
    ),
    remedy="Split the file into cohesive modules.",
)
ENCODING = Rule(
    id="files.encoding",
    title="Text file is not valid UTF-8",
    requirement="R04",
    explanation="Source, test, documentation, and configuration files must be UTF-8 text.",
    remedy="Re-encode the file as UTF-8 or classify it as an asset with a reason.",
)
RULES = (BLOB_SIZE, SOURCE_LINES, ENCODING)


def limit_severity(value: int, limit: Limit) -> Severity | None:
    """Return the severity of a value that exceeds a threshold, if it does."""
    if limit.error is not None and value > limit.error:
        return Severity.ERROR
    if limit.warning is not None and value > limit.warning:
        return Severity.WARNING
    return None


def limit_text(limit: Limit) -> str:
    """Describe a threshold for diagnostic messages."""
    parts = [f"warning above {limit.warning}"] if limit.warning is not None else []
    if limit.error is not None:
        parts.append(f"error above {limit.error}")
    return ", ".join(parts)


def check_blob(path: str, size: int, limit: int, *, origin: str = "") -> Diagnostic | None:
    """Report a blob larger than the limit; `origin` names a history blob."""
    if size <= limit:
        return None
    message = f"{origin}blob is {size} bytes; the limit is {limit} bytes"
    return BLOB_SIZE.diagnostic(message, location=Location(path))


def check_files(files: Iterable[CandidateFile], thresholds: Thresholds) -> Iterator[Diagnostic]:
    """Apply the blob, length, and encoding rules to every candidate file."""
    for file in files:
        found = check_blob(file.path, file.size, thresholds.blob_bytes)
        if found is not None:
            yield found
        if file.data is None:
            continue
        if file.category in TEXT_CATEGORIES and file.text() is None:
            yield ENCODING.diagnostic("file is not valid UTF-8", location=Location(file.path))
        if file.category in {Category.SOURCE, Category.TESTS}:
            lines = physical_lines(file.data)
            limit = thresholds.source_file_lines
            severity = limit_severity(lines, limit)
            if severity is not None:
                yield SOURCE_LINES.diagnostic(
                    f"{lines} lines ({limit_text(limit)})",
                    location=Location(file.path),
                    component=file.record.component,
                    severity=severity,
                )
