"""Rules and the exception type of the Git layer."""

from __future__ import annotations

from quality.diagnostics import Diagnostic, Kind, Rule

GIT_FAILED = Rule(
    id="git.failed",
    title="Git is unavailable or a Git command failed",
    requirement="R03",
    explanation=(
        "Candidates are read through Git plumbing commands. The gate stops when it runs "
        "outside a Git work tree or when Git reports an error."
    ),
    remedy="Run the gate inside a Git work tree with a working 'git' on the system PATH.",
    kind=Kind.ENVIRONMENT,
)
GIT_UNMERGED = Rule(
    id="git.unmerged-index",
    title="The index has unresolved merge conflicts",
    requirement="R03",
    explanation="A candidate with unmerged index entries has no single tree to validate.",
    remedy="Resolve the conflicts, stage the results, and commit again.",
)
GIT_CANDIDATE_CHANGED = Rule(
    id="git.candidate-changed",
    title="The candidate changed while it was validated",
    requirement="R03",
    explanation=(
        "The candidate tree is recomputed before success is reported. A different tree "
        "means the index or revision changed during the run, so the result does not apply."
    ),
    remedy="Run the gate again once nothing else modifies the index or revision.",
    kind=Kind.ENVIRONMENT,
)
GIT_SYMLINK_ESCAPE = Rule(
    id="git.symlink-escape",
    title="A symbolic link points outside the candidate tree",
    requirement="R03",
    explanation=(
        "Symbolic links are materialized only when they resolve inside the candidate, so "
        "source discovery cannot escape the snapshot. Absolute targets are rejected."
    ),
    remedy="Replace the link with a relative link inside the repository or with the file.",
)
GIT_REVISION_UNRESOLVABLE = Rule(
    id="git.revision-unresolvable",
    title="A revision or its history is not available",
    requirement="R03",
    explanation=(
        "Base, head, and tested revisions, their merge base, and the objects between them "
        "must be present. Missing history never skips a gate."
    ),
    remedy="Fetch the full history of the base and head branches (for example fetch-depth 0).",
    kind=Kind.ENVIRONMENT,
)
SNAPSHOT_NOT_ISOLATED = Rule(
    id="git.snapshot-not-isolated",
    title="The snapshot location is not isolated",
    requirement="R03",
    explanation=(
        "Tools search parent directories for configuration and dependencies. A project "
        "marker above the snapshot directory could leak into the candidate's results."
    ),
    remedy="Remove the reported file or point TMPDIR at a directory without project markers.",
    kind=Kind.ENVIRONMENT,
)
GIT_VERSION = Rule(
    id="git.version",
    title="The installed Git is older than the supported minimum",
    requirement="R02",
    explanation=(
        "The gate relies on Git 2.34 or newer for its plumbing output and hook behavior. "
        "Older versions are not tested and may report candidates incorrectly."
    ),
    remedy="Install Git 2.34 or newer and make it the first 'git' on the PATH.",
    kind=Kind.ENVIRONMENT,
)
RULES = (
    GIT_FAILED,
    GIT_VERSION,
    GIT_UNMERGED,
    GIT_CANDIDATE_CHANGED,
    GIT_SYMLINK_ESCAPE,
    GIT_REVISION_UNRESOLVABLE,
    SNAPSHOT_NOT_ISOLATED,
)


class GitioError(Exception):
    """A Git or snapshot failure carrying the diagnostic that explains it."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic
