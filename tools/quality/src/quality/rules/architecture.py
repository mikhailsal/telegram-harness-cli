"""Declared module boundaries: forbidden edges, layers, and cycles (R04)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from quality.diagnostics import Diagnostic, Location, Rule
from quality.policy import Component

FORBIDDEN = Rule(
    id="architecture.forbidden-dependency",
    title="Dependency between declared modules is forbidden",
    requirement="R04",
    explanation=(
        "A component may declare modules and forbid dependencies between them, either as "
        "explicit edges or by ordering modules in layers that only depend downwards."
    ),
    remedy="Remove the import or move the code so that the dependency points downwards.",
)
CYCLE = Rule(
    id="architecture.cycle",
    title="Declared modules depend on each other in a cycle",
    requirement="R04",
    explanation="Declared modules must form an acyclic dependency graph.",
    remedy="Break the cycle by moving shared code into a lower module.",
)
AMBIGUOUS = Rule(
    id="architecture.ambiguous-module",
    title="File belongs to several declared modules",
    requirement="R04",
    explanation="Module path patterns must not overlap, so every file has one module.",
    remedy="Narrow the module path patterns so that each file matches one module.",
)
RULES = (FORBIDDEN, CYCLE, AMBIGUOUS)

type Edges = Mapping[str, Mapping[str, int]]


@dataclass(frozen=True, slots=True)
class Evidence:
    """The first import that creates a module dependency."""

    path: str
    line: int


def _relative(component: Component, path: str) -> str:
    return path if component.root == "." else path.removeprefix(component.root + "/")


def assign_modules(component: Component, paths: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map files to their declared module; also return files matched by several modules."""
    assigned: dict[str, str] = {}
    ambiguous: list[str] = []
    for path in paths:
        pure = PurePosixPath(_relative(component, path))
        names = [
            module.name
            for module in component.architecture.modules
            if any(pure.full_match(pattern) for pattern in module.paths)
        ]
        if len(names) > 1:
            ambiguous.append(path)
        elif names:
            assigned[path] = names[0]
    return assigned, ambiguous


def module_edges(assigned: Mapping[str, str], edges: Edges) -> dict[tuple[str, str], Evidence]:
    """Lift file dependencies to module dependencies, keeping the first evidence."""
    found: dict[tuple[str, str], Evidence] = {}
    for source in sorted(edges):
        for target, line in sorted(edges[source].items(), key=lambda item: (item[1], item[0])):
            pair = (assigned.get(source), assigned.get(target))
            match pair:
                case (str() as left, str() as right) if left != right:
                    found.setdefault((left, right), Evidence(source, line))
                case _:
                    pass
    return found


def strongly_connected(nodes: list[str], graph: Mapping[str, list[str]]) -> list[list[str]]:
    """Return the cycles of a small graph as sorted strongly connected components."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for target in graph.get(node, []):
            if target not in index:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], index[target])
        if low[node] == index[node]:
            members: list[str] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                members.append(member)
                if member == node:
                    break
            if len(members) > 1:
                found.append(sorted(members))

    for node in nodes:
        if node not in index:
            visit(node)
    return sorted(found)


def check_architecture(
    component: Component, paths: list[str], edges: Edges
) -> Iterator[Diagnostic]:
    """Report forbidden, upward, and cyclic dependencies between declared modules."""
    assigned, ambiguous = assign_modules(component, paths)
    for path in ambiguous:
        yield AMBIGUOUS.diagnostic(
            "file matches several declared modules",
            location=Location(path),
            component=component.id,
        )
    lifted = module_edges(assigned, edges)
    yield from _forbidden(component, lifted)
    yield from _cycles(component, lifted)


def _forbidden(
    component: Component, lifted: Mapping[tuple[str, str], Evidence]
) -> Iterator[Diagnostic]:
    rank = {name: position for position, name in enumerate(component.architecture.layers)}
    forbidden = {(edge.source, edge.target) for edge in component.architecture.forbidden}
    for (source, target), evidence in sorted(lifted.items()):
        location = Location(evidence.path, evidence.line)
        if (source, target) in forbidden:
            message = f"module '{source}' must not depend on '{target}'"
        elif source in rank and target in rank and rank[target] < rank[source]:
            message = f"layer '{source}' must not depend on the higher layer '{target}'"
        else:
            continue
        yield FORBIDDEN.diagnostic(message, location=location, component=component.id)


def _cycles(
    component: Component, lifted: Mapping[tuple[str, str], Evidence]
) -> Iterator[Diagnostic]:
    graph: dict[str, list[str]] = {}
    for source, target in sorted(lifted):
        graph.setdefault(source, []).append(target)
    names = [module.name for module in component.architecture.modules]
    for cycle in strongly_connected(names, graph):
        first = lifted[next((a, b) for (a, b) in sorted(lifted) if a in cycle and b in cycle)]
        yield CYCLE.diagnostic(
            "modules depend on each other: " + " <-> ".join(cycle),
            location=Location(first.path, first.line),
            component=component.id,
        )
