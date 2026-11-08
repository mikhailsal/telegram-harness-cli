import pytest

from quality import strictjson
from quality.strictjson import MAX_NESTING, TOO_DEEP, loads


def arrays(levels: int) -> str:
    return "[" * levels + "]" * levels


def objects(levels: int) -> str:
    return '{"a": ' * levels + "1" + "}" * levels


def test_documents_up_to_the_bound_parse() -> None:
    assert loads('{"a": [1, 2.5, true, null]}') == {"a": [1, 2.5, True, None]}
    assert loads("1") == 1
    assert isinstance(loads(arrays(MAX_NESTING)), list)
    assert isinstance(loads(objects(MAX_NESTING)), dict)


@pytest.mark.parametrize(
    "text",
    [arrays(MAX_NESTING + 1), objects(MAX_NESTING + 1), arrays(100_000)],
    ids=["arrays", "objects", "beyond-the-stack"],
)
def test_deeper_documents_fail_alike_on_every_machine(text: str) -> None:
    with pytest.raises(ValueError, match=f"^{TOO_DEEP}$"):
        loads(text)


def test_a_decoder_out_of_stack_fails_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    def overflow(*_args: object, **_options: object) -> object:
        msg = "Stack overflow"
        raise RecursionError(msg)

    monkeypatch.setattr(strictjson.json, "loads", overflow)
    with pytest.raises(ValueError, match=f"^{TOO_DEEP}$"):
        loads("[]")


@pytest.mark.parametrize("text", ["[NaN]", "[Infinity]", '{"a": -Infinity}'])
def test_non_standard_constants_are_rejected(text: str) -> None:
    with pytest.raises(ValueError, match=r"^non-standard JSON constant"):
        loads(text)


def test_a_pairs_hook_builds_the_objects() -> None:
    assert loads('{"b": 1, "a": 2}', sorted) == [("a", 2), ("b", 1)]
