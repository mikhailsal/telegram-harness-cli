"""Validator provenance: a candidate is judged by its own core (decision 0004).

The launcher runs the core from the working tree. Before a verdict command
decides anything, the core compares the files that decide verdicts, its package,
its toolchain manifest, the sources and manifest of the TypeScript helper it
runs on Node, and the sources, manifests, and lockfiles of the Dart helper it
compiles and the PHP helper it installs, with the candidate's. When they
differ, the candidate's core is materialized and runs the same command as a
child process, so unstaged edits to the validator do not change the verdict.
The helpers' installed `node_modules`, `.dart_tool`, and `vendor` do not judge:
the TypeScript helper imports TypeScript and ESLint from the checked
component, and the Dart and PHP helpers are built from their judged files and
locked packages. The comparison itself runs from the working
tree; CI runs the committed core and is the boundary for edits to this step.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from quality.diagnostics import Diagnostic, Kind, Location, Rule
from quality.gitio import Repository, blob_id, list_tree, materialize
from quality.gitio.snapshot import CANDIDATE_DIRECTORY, SNAPSHOT_PREFIX
from quality.gitio.tree import EXECUTABLE, REGULAR, SYMLINK
from quality.process import Command, run_attached

CORE = "tools/quality"
PACKAGE = "src/quality"
MANIFEST = "toolchains.toml"
LOCK = "uv.lock"
HELPER = "helpers/typescript"
HELPER_SOURCE = f"{HELPER}/src"
HELPER_MANIFEST = f"{HELPER}/package.json"
DART_HELPER = "helpers/dart"
PHP_HELPER = "helpers/php"
CACHE = "__pycache__"
NODE_MODULES = "node_modules"
DART_TOOL = ".dart_tool"
VENDOR = "vendor"
MANIFESTS = (
    MANIFEST,
    HELPER_MANIFEST,
    f"{DART_HELPER}/pubspec.yaml",
    f"{DART_HELPER}/pubspec.lock",
    f"{PHP_HELPER}/composer.json",
    f"{PHP_HELPER}/composer.lock",
)
# The source trees that judge, each with the directory name a walk skips.
TREES = (
    (PACKAGE, CACHE),
    (HELPER_SOURCE, NODE_MODULES),
    (f"{DART_HELPER}/bin", DART_TOOL),
    (f"{DART_HELPER}/lib", DART_TOOL),
    (f"{PHP_HELPER}/bin", VENDOR),
    (f"{PHP_HELPER}/src", VENDOR),
)
CHILD_TIMEOUT = 6 * 3600.0
# The launcher runs the core with the same statement; it puts the source root first.
BOOT = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); sys.argv[0] = 'quality'; "
    "from quality.cli.main import main; raise SystemExit(main())"
)

ENVIRONMENT = Rule(
    id="core.environment",
    title="The core environment does not match the candidate's core lockfile",
    requirement="R03",
    explanation=(
        "A candidate is judged by its own core, which runs in the core environment the "
        "launcher syncs from tools/quality/uv.lock in the working tree. When the candidate's "
        "lockfile differs, its core would run with packages it does not lock."
    ),
    remedy=(
        "Stage or restore tools/quality/uv.lock so that the working tree and the candidate "
        "agree, then run the command again."
    ),
    kind=Kind.ENVIRONMENT,
)
RULES = (ENVIRONMENT,)


class ProvenanceError(RuntimeError):
    """The materialized candidate core is not the candidate's core; an internal error."""


class Provenance(StrEnum):
    """How the running core relates to the candidate's core."""

    NOT_APPLICABLE = "not applicable"
    CURRENT = "current"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class CandidateCore:
    """The core of a candidate: its tree, the files that judge, and its lockfile blob."""

    tree: str
    files: Mapping[str, str]
    lock: str | None

    @property
    def algorithm(self) -> str:
        """The hash algorithm of the repository's object IDs."""
        return "sha256" if len(self.tree) == 64 else "sha1"


def _judges(path: str) -> bool:
    """Whether a core file decides verdicts: the package and helper sources, and manifests."""
    parts = path.split("/")
    in_tree = any(path.startswith(f"{tree}/") and skipped not in parts for tree, skipped in TREES)
    return in_tree or path in MANIFESTS


def candidate_core(repo: Repository, tree: str) -> CandidateCore | None:
    """Return the core of a candidate tree, or None when the candidate has none.

    Files are named relative to the core directory and identified as
    `<mode> <object ID>`, the way `running_files` identifies files on disk. A
    candidate without a core package has no core.
    """
    listing = repo.git("ls-tree", "-z", tree, "--", CORE).stdout
    meta = listing.split(b"\t", 1)[0].split()
    if len(meta) != 3 or meta[1] != b"tree":
        return None
    core = meta[2].decode("ascii")
    entries = list_tree(repo, core)
    files = {entry.path: f"{entry.mode} {entry.oid}" for entry in entries if _judges(entry.path)}
    if not any(path.startswith(f"{PACKAGE}/") for path in files):
        return None
    lock = next((entry.oid for entry in entries if entry.path == LOCK and entry.is_file), None)
    return CandidateCore(core, files, lock)


def _identity(path: Path, algorithm: str) -> str:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        target = os.fsencode(path.readlink())
        return f"{SYMLINK} {blob_id(target, algorithm=algorithm)}"
    if stat.S_ISREG(info.st_mode):
        mode = EXECUTABLE if info.st_mode & stat.S_IXUSR else REGULAR
        return f"{mode} {blob_id(path.read_bytes(), algorithm=algorithm)}"
    return "special"


def running_files(root: Path, algorithm: str = "sha1") -> dict[str, str]:
    """Identify the files of a core directory that judge, as their tree entries would."""
    found: dict[str, str] = {}
    for name in MANIFESTS:
        manifest = root / name
        if manifest.is_symlink() or manifest.exists():
            found[name] = _identity(manifest, algorithm)
    for tree, skipped in TREES:
        for directory, directories, files in (root / tree).walk():
            directories[:] = [name for name in directories if name != skipped]
            for name in files:
                path = directory / name
                found[path.relative_to(root).as_posix()] = _identity(path, algorithm)
    return found


def provenance(core: CandidateCore | None, root: Path) -> Provenance:
    """Compare the running core in directory `root` with the candidate's core."""
    if core is None:
        return Provenance.NOT_APPLICABLE
    same = running_files(root, core.algorithm) == core.files
    return Provenance.CURRENT if same else Provenance.STALE


def lock_findings(core: CandidateCore, lock: Path) -> list[Diagnostic]:
    """Report a candidate core lockfile that differs from the one the environment came from."""
    try:
        synced: str | None = blob_id(lock.read_bytes(), algorithm=core.algorithm)
    except OSError:
        synced = None
    if synced == core.lock:
        return []
    message = (
        f"the candidate's {CORE}/{LOCK} differs from {lock}, "
        "which the core environment was synced from"
    )
    return [ENVIRONMENT.diagnostic(message, location=Location(f"{CORE}/{LOCK}"))]


def in_snapshot(root: Path) -> bool:
    """Whether the core in directory `root` is a materialized candidate core."""
    parts = root.parts
    return (
        len(parts) >= 2
        and parts[-2].startswith(SNAPSHOT_PREFIX)
        and parts[-1] == CANDIDATE_DIRECTORY
    )


def run_candidate_core(
    repo: Repository, core: CandidateCore, argv: Sequence[str], environ: Mapping[str, str]
) -> int:
    """Run `quality <argv>` with the candidate's core in this directory; return its status."""
    with materialize(repo, core.tree) as snapshot:
        if running_files(snapshot.root, core.algorithm) != core.files:
            message = f"the materialized core in {snapshot.root} differs from tree {core.tree}"
            raise ProvenanceError(message)
        source = str(snapshot.root / Path(PACKAGE).parent)
        command = (sys.executable, "-I", "-c", BOOT, source, *argv)
        return run_attached(Command(command, cwd=Path.cwd(), timeout=CHILD_TIMEOUT, env=environ))
