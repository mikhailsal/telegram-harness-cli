"""Python structure metrics from the standard `ast` and `tokenize` modules.

The counting rules are specified in decision 0007; this module is their reference
implementation for Python.
"""

from __future__ import annotations

import ast
import io
import tokenize
from collections.abc import Iterator
from dataclasses import dataclass

from quality.metrics import FileMetrics, FunctionMetrics, ImportRef, StructureError

type Function = ast.FunctionDef | ast.AsyncFunctionDef

NON_CODE_TOKENS = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def code_lines(source: bytes) -> frozenset[int]:
    """Return the physical lines holding a token other than comments and layout."""
    lines: set[int] = set()
    for token in tokenize.tokenize(io.BytesIO(source).readline):
        if token.type not in NON_CODE_TOKENS:
            lines.update(range(token.start[0], token.end[0] + 1))
    return frozenset(lines)


def _decisions(node: ast.AST) -> int:
    match node:
        case ast.If() | ast.IfExp() | ast.For() | ast.AsyncFor() | ast.While():
            return 1
        case ast.ExceptHandler():
            return 1
        case ast.BoolOp(values=values):
            return len(values) - 1
        case ast.comprehension(ifs=ifs):
            return 1 + len(ifs)
        case ast.match_case(pattern=ast.MatchAs(pattern=None), guard=None):
            return 0
        case ast.match_case():
            return 1
        case _:
            return 0


def _own_nodes(body: list[ast.stmt]) -> Iterator[ast.AST]:
    """Yield the nodes of a body without entering nested functions or classes.

    Lambdas are expressions of the enclosing function and are entered.
    """
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def complexity(function: Function) -> int:
    """Return the cyclomatic complexity of a function body."""
    return 1 + sum(_decisions(node) for node in _own_nodes(function.body))


@dataclass(frozen=True, slots=True)
class _Nesting:
    lines: list[bytes]

    def is_elif(self, node: ast.If) -> bool:
        return self.lines[node.lineno - 1][node.col_offset :].startswith(b"elif")

    def depth(self, body: list[ast.stmt]) -> int:
        return max((self.statement(stmt) for stmt in body), default=0)

    def if_branches(self, node: ast.If) -> list[list[ast.stmt]]:
        branches = [node.body]
        rest = node.orelse
        while len(rest) == 1 and isinstance(nested := rest[0], ast.If) and self.is_elif(nested):
            branches.append(nested.body)
            rest = nested.orelse
        return [*branches, rest]

    def statement(self, node: ast.stmt) -> int:
        match node:
            case ast.If():
                inner = [self.depth(branch) for branch in self.if_branches(node)]
            case ast.For() | ast.AsyncFor() | ast.While():
                inner = [self.depth(node.body), self.depth(node.orelse)]
            case ast.With() | ast.AsyncWith():
                inner = [self.depth(node.body)]
            case ast.Try() | ast.TryStar():
                handlers = [self.depth(handler.body) for handler in node.handlers]
                inner = [self.depth(node.body), *handlers]
                inner += [self.depth(node.orelse), self.depth(node.finalbody)]
            case ast.Match():
                inner = [self.depth(case.body) for case in node.cases]
            case _:
                return 0
        return 1 + max(inner)


def parameters(function: Function, *, in_class: bool) -> int:
    """Count explicit parameters, excluding the receiver of instance and class methods."""
    arguments = function.args
    count = len(arguments.posonlyargs) + len(arguments.args) + len(arguments.kwonlyargs)
    count += (arguments.vararg is not None) + (arguments.kwarg is not None)
    static = any(
        isinstance(decorator, ast.Name) and decorator.id == "staticmethod"
        for decorator in function.decorator_list
    )
    has_receiver = bool(arguments.posonlyargs or arguments.args)
    return count - 1 if in_class and not static and has_receiver else count


def _docstring_lines(function: Function) -> set[int]:
    first = function.body[0]
    match first:
        case ast.Expr(value=ast.Constant(value=str())):
            return set(range(first.lineno, (first.end_lineno or first.lineno) + 1))
        case _:
            return set()


@dataclass(slots=True)
class _Collector:
    code: frozenset[int]
    nesting: _Nesting
    functions: list[FunctionMetrics]

    def visit(self, body: list[ast.stmt], prefix: str, *, in_class: bool) -> None:
        for node in body:
            match node:
                case ast.FunctionDef() | ast.AsyncFunctionDef():
                    self.measure(node, prefix, in_class=in_class)
                    self.visit(node.body, f"{prefix}{node.name}.", in_class=False)
                case ast.ClassDef():
                    self.visit(node.body, f"{prefix}{node.name}.", in_class=True)
                case _:
                    self.visit(_child_statements(node), prefix, in_class=False)

    def measure(self, node: Function, prefix: str, *, in_class: bool) -> None:
        end = node.end_lineno or node.lineno
        span = set(range(node.body[0].lineno, end + 1)) - _docstring_lines(node)
        self.functions.append(
            FunctionMetrics(
                name=prefix + node.name,
                line=node.lineno,
                code_lines=len(span & self.code),
                complexity=complexity(node),
                nesting=self.nesting.depth(node.body),
                parameters=parameters(node, in_class=in_class),
            )
        )


def _child_statements(node: ast.stmt) -> list[ast.stmt]:
    """Statements nested in compound statements, where functions may be defined."""
    found: list[ast.stmt] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            found.append(child)
        elif isinstance(child, ast.ExceptHandler | ast.match_case):
            found.extend(child.body)
    return found


def _imports(tree: ast.Module) -> tuple[ImportRef, ...]:
    found: list[ImportRef] = []
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=names):
                found.extend(ImportRef(module=alias.name, line=node.lineno) for alias in names)
            case ast.ImportFrom(module=module, level=level, names=names):
                imported = tuple(alias.name for alias in names if alias.name != "*")
                found.append(ImportRef(module or "", node.lineno, level, imported))
            case _:
                pass
    return tuple(sorted(found, key=lambda ref: (ref.line, ref.module)))


class PythonStructure:
    """The Python implementation of the structure-metric capability."""

    def analyze(self, path: str, source: bytes) -> FileMetrics:
        """Measure every function of a Python file and list its imports."""
        try:
            tree = ast.parse(source, filename=path, type_comments=False)
            collector = _Collector(code_lines(source), _Nesting(source.splitlines()), [])
            collector.visit(tree.body, "", in_class=False)
            imports = _imports(tree)
        except SyntaxError as exc:
            raise StructureError(path, exc.lineno, exc.msg) from exc
        except (RecursionError, MemoryError) as exc:
            # CPython's parser signals pathological nesting with either error.
            raise StructureError(path, None, "nesting too deep to analyze") from exc
        functions = sorted(collector.functions, key=lambda f: (f.line, f.name))
        return FileMetrics(path=path, functions=tuple(functions), imports=imports)
