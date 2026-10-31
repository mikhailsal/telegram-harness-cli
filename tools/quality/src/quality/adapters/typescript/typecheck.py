"""The TypeScript compiler, the type checker of the TypeScript profile.

The helper checks every project of the component's tsconfig.json and its
project references through the compiler API, reading referenced projects from
source so nothing needs to be built. Each project must keep the strict family
on, and every TypeScript source and test file must belong to a project, or the
compiler never sees it. Errors and warnings fail; suggestions do not.
"""

from __future__ import annotations

import posixpath
from pathlib import Path

from quality.adapters.target import Target, location
from quality.adapters.typescript import helper
from quality.adapters.typescript.config import TSCONFIG
from quality.adapters.typescript.environment import Node
from quality.diagnostics import Diagnostic, Location, Rule

TYPES = Rule(
    id="typescript.types",
    title="TypeScript strict type error",
    requirement="R04",
    explanation=(
        "Source and test files are checked by the component's pinned TypeScript compiler over "
        "tsconfig.json and its project references, in strict mode. A file that belongs to no "
        "project is never checked and fails."
    ),
    remedy="Fix the type error; do not weaken the configuration or add ignore comments.",
)
RULES = (TYPES,)

REPORTED = frozenset({"error", "warning"})


class TypeReport:
    """Turns the helper's answer into findings of one component."""

    def __init__(self, target: Target) -> None:
        self.target = target
        self.config = posixpath.normpath(posixpath.join(target.component.root, TSCONFIG))
        self.found: list[Diagnostic] = []

    def add(self, message: str, where: Location) -> None:
        """Record one finding."""
        self.found.append(
            TYPES.diagnostic(message, location=where, component=self.target.component.id)
        )

    def projects(self, response: helper.Entry) -> set[str]:
        """Report weakened projects; return the files the projects check."""
        members: set[str] = set()
        for project in helper.entries(response, "projects"):
            config = helper.repository_path(self.target.snapshot, helper.text(project, "config"))
            for problem in helper.texts(project, "problems"):
                self.add(
                    f"{problem}; the profile requires strict mode", Location(config or self.config)
                )
            for name in helper.texts(project, "files"):
                path = helper.repository_path(self.target.snapshot, name)
                if path is not None:
                    members.add(path)
        return members

    def diagnostic(self, entry: helper.Entry) -> None:
        """Report one compiler diagnostic at its file, or at tsconfig.json."""
        if helper.text(entry, "category") not in REPORTED:
            return
        message = f"TS{helper.integer(entry, 'code')}: {helper.text(entry, 'message')}"
        name = helper.optional_text(entry, "file")
        path = None if name is None else helper.repository_path(self.target.snapshot, name)
        if path is None:
            prefix = f"{name}: " if name is not None else ""
            self.add(prefix + message, Location(self.config))
            return
        line = helper.optional_integer(entry, "line")
        self.add(message, location(path, line, helper.optional_integer(entry, "column")))


def typescript_types(
    target: Target, *, node: Node, work: Path, program: Path = helper.HELPER
) -> list[Diagnostic]:
    """Type-check the component's projects; report files no project checks."""
    request = {
        "typescript": str(target.directory),
        "tsconfig": str(target.directory / TSCONFIG),
    }
    response = helper.run_helper(
        helper.Program(node, program), "types", request, work=work, cwd=target.directory
    )
    report = TypeReport(target)
    members = report.projects(response)
    for path in target.files:
        if path not in members:
            report.add(
                f"no project of {TSCONFIG} includes the file, so it is not type-checked",
                Location(path),
            )
    for entry in helper.entries(response, "diagnostics"):
        report.diagnostic(entry)
    return report.found
