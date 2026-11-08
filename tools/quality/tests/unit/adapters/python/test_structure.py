import textwrap

import pytest

from quality.adapters.python.structure import PythonStructure, code_lines
from quality.metrics import FunctionMetrics, ImportRef, StructureError


def measure(source: str) -> dict[str, FunctionMetrics]:
    metrics = PythonStructure().analyze("m.py", textwrap.dedent(source).encode())
    return {f.name: f for f in metrics.functions}


def only(source: str) -> FunctionMetrics:
    (function,) = measure(source).values()
    return function


def test_code_lines_skip_blank_and_comment_lines_but_keep_strings() -> None:
    source = b'x = 1\n\n# comment\ny = """a\n\nb"""  # trailing\n'
    assert code_lines(source) == {1, 4, 5, 6}


def test_function_code_lines_exclude_signature_docstring_comments_and_blanks() -> None:
    function = only(
        '''
        @decorator
        def f(
            a,
        ):
            """Docstring
            over two lines."""
            # comment

            x = [
                a,
            ]
            return x
        '''
    )
    assert function.code_lines == 4
    assert function.line == 3


def test_one_line_functions_have_one_code_line() -> None:
    assert only("def f(): return 1\n").code_lines == 1


def test_nested_functions_count_inside_their_parent_and_alone() -> None:
    found = measure(
        """
        def outer():
            def inner():
                return 1
            return inner
        """
    )
    assert list(found) == ["outer", "outer.inner"]
    assert found["outer"].code_lines == 3
    assert found["outer.inner"].code_lines == 1


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("return 1", 1),
        ("if a:\n    pass\nelif b:\n    pass\nelse:\n    pass", 3),
        ("return a if b else c", 2),
        ("for x in y:\n    pass\nelse:\n    pass", 2),
        ("async for x in y:\n    pass", 2),
        ("while a:\n    pass", 2),
        ("try:\n    pass\nexcept A:\n    pass\nexcept B:\n    pass\nfinally:\n    pass", 3),
        ("try:\n    pass\nexcept* A:\n    pass", 2),
        ("with a, b:\n    pass", 1),
        ("assert a", 1),
        ("return a and b or c", 3),
        ("return [x for x in y if x if not x for z in x]", 5),
        ("return lambda: a or b", 2),
        (
            (
                "match a:\n    case 1:\n        pass\n    case [x] if x:\n        pass\n"
                "    case _:\n        pass"
            ),
            3,
        ),
        ("match a:\n    case x:\n        pass", 1),
        ("match a:\n    case _ if a:\n        pass", 2),
        ("def inner():\n    if a:\n        pass\nclass C:\n    if b:\n        pass", 1),
    ],
    ids=[
        "straight",
        "if-elif-else",
        "ternary",
        "for-else",
        "async-for",
        "while",
        "except-clauses",
        "except-star",
        "with",
        "assert",
        "boolean-operators",
        "comprehension",
        "lambda",
        "match",
        "capture-pattern",
        "guarded-wildcard",
        "nested-scopes",
    ],
)
def test_cyclomatic_complexity(body: str, expected: int) -> None:
    source = "async def f(a, b, c, y):\n" + textwrap.indent(body, "    ") + "\n"
    assert measure(source)["f"].complexity == expected


def test_decisions_in_nested_functions_are_their_own() -> None:
    found = measure(
        """
        def outer(a):
            def inner(b):
                return b or a
            return inner
        """
    )
    assert (found["outer"].complexity, found["outer.inner"].complexity) == (1, 2)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("return 1", 0),
        ("if a:\n    pass", 1),
        ("if a:\n    pass\nelif b:\n    if c:\n        pass", 2),
        ("if a:\n    pass\nelse:\n    if b:\n        if c:\n            pass", 3),
        ("for x in y:\n    while x:\n        with x:\n            pass", 3),
        ("for x in y:\n    pass\nelse:\n    if x:\n        pass", 2),
        ("try:\n    pass\nexcept A:\n    if a:\n        pass", 2),
        ("try:\n    pass\nfinally:\n    if a:\n        pass", 2),
        ("match a:\n    case 1:\n        if b:\n            pass", 2),
        ("x = [y for y in z if y]", 0),
        ("def inner():\n    if a:\n        if b:\n            pass", 0),
    ],
    ids=[
        "flat",
        "if",
        "elif-is-not-deeper",
        "else-if-is-deeper",
        "loops-and-with",
        "loop-else",
        "handler",
        "finally",
        "match",
        "comprehension",
        "nested-function",
    ],
)
def test_nesting_depth(body: str, expected: int) -> None:
    source = "def f(a, b, c, y, z):\n" + textwrap.indent(body, "    ") + "\n"
    assert measure(source)["f"].nesting == expected


def test_parameters_exclude_only_the_implicit_receiver() -> None:
    found = measure(
        """
        def free(a, /, b, *args, c, **kwargs): pass
        def empty(): pass
        class C:
            def method(self, a, b): pass
            @classmethod
            def build(cls, a): pass
            @staticmethod
            def helper(a, b): pass
            def broken(*args): pass
            def inner(self):
                def local(x): pass
        """
    )
    assert {name: f.parameters for name, f in found.items()} == {
        "free": 5,
        "empty": 0,
        "C.method": 2,
        "C.build": 1,
        "C.helper": 2,
        "C.broken": 1,
        "C.inner": 0,
        "C.inner.local": 1,
    }


def test_functions_in_compound_statements_are_found() -> None:
    found = measure(
        """
        import typing
        if typing.TYPE_CHECKING:
            def typed(): pass
        try:
            def guarded(): pass
        except ImportError:
            def fallback(): pass
        match typing:
            case _:
                def matched(): pass
        """
    )
    assert list(found) == ["typed", "guarded", "fallback", "matched"]


def test_imports_are_listed_as_written() -> None:
    metrics = PythonStructure().analyze(
        "pkg/m.py",
        b"import os, a.b as c\nfrom . import x\nfrom ..up import y\nfrom s import *\n"
        b"def f():\n    import z\n",
    )
    assert metrics.imports == (
        ImportRef("a.b", 1),
        ImportRef("os", 1),
        ImportRef("", 2, 1, ("x",)),
        ImportRef("up", 3, 2, ("y",)),
        ImportRef("s", 4),
        ImportRef("z", 6),
    )


@pytest.mark.parametrize(
    ("source", "line", "message"),
    [
        (b"def f(:\n", 1, "invalid syntax"),
        (b"x = 1\0\n", None, "null bytes"),
        (b"x = " + b"(" * 300 + b")" * 300 + b"\n", 1, "too many nested parentheses"),
        (b"x = 1" + b" + 1" * 200_000 + b"\n", None, "nesting too deep to analyze"),
        (b"x = " + b"-" * 100_000 + b"1\n", None, "nesting too deep to analyze"),
    ],
    ids=["syntax", "null-byte", "deep", "long-chain", "deep-unary"],
)
def test_unparseable_files_raise(source: bytes, line: int | None, message: str) -> None:
    with pytest.raises(StructureError) as raised:
        PythonStructure().analyze("bad.py", source)
    assert raised.value.path == "bad.py"
    assert raised.value.line == line
    assert message in raised.value.message
