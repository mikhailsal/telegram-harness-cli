"""Resolve the local GitLab wrapper's jobs for the shared gate lane contract."""

from __future__ import annotations

import posixpath
from collections.abc import Iterator, Mapping
from typing import cast

from quality.ci.lanes import GATE, LANES, check_lanes, gate_lanes
from quality.ci.workflow import Table, finding, items, mapping
from quality.diagnostics import Diagnostic

ROOT = ".gitlab-ci.yml"
REFERENCE = "ci/gitlab/quality.gitlab-ci.yml"
PATHS = frozenset({ROOT, REFERENCE})
KEYWORDS = frozenset(
    {
        "after_script",
        "before_script",
        "cache",
        "default",
        "image",
        "include",
        "services",
        "spec",
        "stages",
        "types",
        "variables",
        "workflow",
    }
)


def _merge(base: Table, override: Table) -> dict[str, object]:
    merged = dict(base)
    for key, value in override.items():
        before, after = mapping(merged.get(key)), mapping(value)
        merged[key] = _merge(before, after) if before is not None and after is not None else value
    return merged


def _resolve(document: Table, name: str, chain: tuple[str, ...] = ()) -> Table | None:
    job = mapping(document.get(name))
    if job is None or name in chain:
        return None
    extends = job.get("extends", [])
    if not isinstance(extends, (str, list)):
        return None
    parents = [extends] if isinstance(extends, str) else items(job.get("extends"))
    merged: Table = {}
    for parent in parents:
        base = _resolve(document, str(parent), (*chain, name))
        if base is None:
            return None
        merged = _merge(merged, base)
    return _merge(merged, job)


def _script(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    for item in items(value):
        yield from _script(item)


def _required(job: Table) -> bool:
    return (
        job.get("allow_failure", False) is False
        and job.get("when", "on_success") in ("on_success", "always")
        and not any(key in job for key in ("rules", "only", "except"))
    )


def _jobs(document: Table) -> Iterator[tuple[str, Table | None]]:
    for name, value in document.items():
        job = mapping(value)
        if (
            name.startswith(".")
            or name in KEYWORDS
            or job is None
            or not {"script", "extends"}.intersection(job)
        ):
            continue
        yield name, _resolve(document, name)


def _has_gate(job: Table) -> bool:
    return any(GATE.search(text) for text in _script(job.get("script")))


def _hidden_gate(document: Table) -> bool:
    for name, value in document.items():
        job = mapping(value)
        if name.startswith(".") and job is not None and _has_gate(job):
            return True
    return False


def _lane_job(document: Table, job: Table) -> Table:
    variables = _merge(
        mapping(document.get("variables")) or {}, mapping(job.get("variables")) or {}
    )
    return {"env": variables, "steps": [{"run": text} for text in _script(job.get("script"))]}


def _runs_required_gate(jobs: list[Table]) -> bool:
    return any(
        list(gate_lanes(text))
        for job in jobs
        if _required(job)
        for text in _script(job.get("script"))
    )


def check_gitlab_lanes(path: str, document: Table) -> Iterator[Diagnostic]:
    """Check runnable local jobs, resolving extends and job-over-global variables."""
    gate_jobs: list[Table] = []
    for name, resolved in _jobs(document):
        if resolved is None:
            message = f"job '{name}' has an unresolved or cyclic extends chain"
            yield finding(LANES, path, message, name)
        elif _has_gate(resolved):
            gate_jobs.append(resolved)
    required = [_lane_job(document, job) for job in gate_jobs if _required(job)]
    if not _runs_required_gate(gate_jobs) and (gate_jobs or _hidden_gate(document)):
        message = "the GitLab wrapper defines the gate but no required job runs it"
        yield finding(LANES, path, message, "gate")
    yield from check_lanes(path, required)


def _includes(value: object) -> list[object]:
    if value is None:
        return []
    return cast(list[object], value) if isinstance(value, list) else [value]


def _local_path(value: object) -> str | None:
    local = value if isinstance(value, str) else (mapping(value) or {}).get("local")
    if not isinstance(local, str):
        return None
    return posixpath.normpath(local.lstrip("/"))


def _ordinary_reference(value: object) -> bool:
    return _local_path(value) == REFERENCE and (
        isinstance(value, str) or set(mapping(value) or {}) == {"local"}
    )


def _composition_problem(root: Table, reference: Table) -> str | None:
    if not all(_ordinary_reference(item) for item in _includes(root.get("include"))):
        return "the known GitLab wrapper is combined with conditional or unresolved includes"
    if _includes(reference.get("include")):
        return "the known GitLab wrapper has unresolved nested includes"
    return None


def check_gitlab_documents(documents: Mapping[str, Table | None]) -> Iterator[Diagnostic]:
    """Check a root including the known local wrapper as one merged configuration."""
    root = documents.get(ROOT)
    if root is not None and any(
        _local_path(item) == REFERENCE for item in _includes(root.get("include"))
    ):
        reference = documents.get(REFERENCE)
        if reference is None:
            message = f"the included GitLab wrapper '{REFERENCE}' is missing or unreadable"
            yield finding(LANES, ROOT, message, REFERENCE)
            return
        problem = _composition_problem(root, reference)
        if problem is not None:
            yield finding(LANES, ROOT, problem, "include")
            return
        yield from check_gitlab_lanes(ROOT, _merge(reference, root))
        return
    for path, document in documents.items():
        if document is not None:
            yield from check_gitlab_lanes(path, document)
