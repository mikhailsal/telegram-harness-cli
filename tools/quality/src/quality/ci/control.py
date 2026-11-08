"""Control files: what decides the gate, and so needs owner review when it changes (R09)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import PurePosixPath

from quality.diagnostics import Diagnostic, Location, Rule, Severity
from quality.discovery import FileRecord
from quality.policy import Category, CiPolicy, Policy, Role

CHANGED = Rule(
    id="ci.control-changed",
    title="The change modifies a control file",
    requirement="R09",
    explanation=(
        "At the merge stage the gate lists every changed control file: the policy, the "
        "launcher, files under .github, GitLab entry points, the roots of tooling, helper, "
        "and example components, scenario manifests, native configuration, and the paths "
        "in `[ci].control`. A hosting rule that requires code-owner review is what protects "
        "them; the gate only makes the change visible."
    ),
    remedy="Have a code owner review the control change.",
    severity=Severity.INFO,
)
RULES = (CHANGED,)

FIXED = ("quality.toml", "quality", ".gitattributes", ".github/**", ".gitlab-ci.yml")
OWNED_ROLES = frozenset({Role.TOOLING, Role.HELPER, Role.EXAMPLE})


def _under(root: str, path: str) -> str:
    return path if root == "." else f"{root}/{path}"


def control_patterns(policy: Policy, ci: CiPolicy) -> list[str]:
    """Return the globs of the control files the policy derives, plus `[ci].control`."""
    patterns = [*FIXED, ci.codeowners, ci.workflow, *ci.control]
    for component in policy.components:
        if component.role in OWNED_ROLES:
            patterns.append(_under(component.root, "**"))
        if component.scenarios is not None:
            patterns.append(_under(component.root, component.scenarios))
    return list(dict.fromkeys(patterns))


def is_control(path: str, patterns: Iterable[str]) -> bool:
    """Whether a path matches a control pattern."""
    pure = PurePosixPath(path)
    return any(pure.full_match(pattern) for pattern in patterns)


def control_files(policy: Policy, ci: CiPolicy, records: Iterable[FileRecord]) -> list[str]:
    """Return the candidate's control files: matched by a pattern or native configuration."""
    patterns = control_patterns(policy, ci)
    return [
        record.path
        for record in records
        if record.category is Category.CONFIG or is_control(record.path, patterns)
    ]


def changed_control(
    policy: Policy, ci: CiPolicy, records: Iterable[FileRecord], changed: Iterable[str]
) -> Iterator[Diagnostic]:
    """Report each changed path that is a control file, deleted ones by their patterns."""
    present = set(control_files(policy, ci, records))
    patterns = control_patterns(policy, ci)
    for path in sorted(set(changed)):
        if path in present or is_control(path, patterns):
            message = f"control file '{path}' changed; it needs code-owner review"
            yield CHANGED.diagnostic(message, location=Location(path))
