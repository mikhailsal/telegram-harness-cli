"""Import boundaries of a browser component's source files (decision 0017).

Browser code runs in the page, so it may not import Node builtins, the declared
server-only modules, or the declared server-only packages. The helper resolves
every import of the browser source files with the module resolution of the
project that owns the file, so `paths` aliases are followed. An import the
compiler erases, `import type` or `export type`, loads nothing and is allowed.
Configuration scripts, tests, and the server modules themselves keep their Node
environment; what packages import is checked by the production build.
"""

from __future__ import annotations

from pathlib import Path

from quality.adapters.target import Target
from quality.adapters.typescript import helper
from quality.adapters.typescript.browser import server_module
from quality.adapters.typescript.environment import Node
from quality.adapters.typescript.structure import files_request
from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import Component

BOUNDARIES = Rule(
    id="typescript.boundaries",
    title="Browser code imports a Node builtin or a server-only module",
    requirement="R04",
    explanation=(
        "The source files of a browser component, except its declared server modules, may "
        "not import Node builtins, the `server_modules` of its `[components.browser]` table, "
        "or its `server_packages`. Imports resolve through the component's tsconfig, aliases "
        "included; `import type` and `export type` are allowed because nothing is loaded."
    ),
    remedy=(
        "Keep server code behind an API the page calls, import only its types with "
        "`import type`, or move the file out of the browser source."
    ),
)
RULES = (BOUNDARIES,)


def package_name(specifier: str) -> str:
    """Return the npm package a bare specifier names: `pg` for `pg/lib`, `@a/b` for `@a/b/c`."""
    parts = specifier.split("/")
    return "/".join(parts[:2]) if specifier.startswith("@") else parts[0]


def _violation(component: Component, snapshot: Path, entry: helper.Entry) -> str | None:
    if helper.flag(entry, "typeOnly"):
        return None
    specifier = helper.text(entry, "specifier")
    if helper.flag(entry, "builtin"):
        return f"browser code imports the Node builtin '{specifier}'"
    resolved = helper.optional_text(entry, "resolved")
    path = None if resolved is None else helper.repository_path(snapshot, resolved)
    if path is not None:
        if server_module(component, path):
            return f"browser code imports the server-only module {path}"
        return None
    package = package_name(specifier)
    if package in component.browser_settings.server_packages:
        return f"browser code imports the server-only package '{package}'"
    return None


def import_boundaries(
    target: Target, *, node: Node, work: Path, program: Path = helper.HELPER
) -> list[Diagnostic]:
    """Report each import of the named browser source files that crosses a boundary."""
    response = helper.run_helper(
        helper.Program(node, program),
        "imports",
        files_request(target),
        work=work,
        cwd=target.directory,
    )
    component = target.component
    found: list[Diagnostic] = []
    for file in helper.entries(response, "files"):
        path = target.relative(helper.TOOL, helper.text(file, "file"))
        for entry in helper.entries(file, "imports"):
            message = _violation(component, target.snapshot, entry)
            if message is not None:
                line = helper.integer(entry, "line")
                found.append(
                    BOUNDARIES.diagnostic(
                        message, location=Location(path, line), component=component.id
                    )
                )
    return found
