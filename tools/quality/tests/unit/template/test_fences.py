import pytest

from quality.template import BEGIN, END, FenceError, strip_fences

MARKDOWN = f"""\
# Index

- [Guide](guide.md)

<!-- {BEGIN} -->

## Plan

- [Plan](PLAN.md)

<!-- {END} -->

## Reference

- [CLI](cli.md)
"""
TOML = f"""\
[repository]
# {BEGIN}
mode = "maintainer"
# {END}
default_branch = "main"
"""


def test_a_fenced_region_and_its_fence_lines_are_removed() -> None:
    assert strip_fences(TOML) == '[repository]\ndefault_branch = "main"\n'


def test_removing_a_region_leaves_single_blank_lines() -> None:
    assert (
        strip_fences(MARKDOWN)
        == "# Index\n\n- [Guide](guide.md)\n\n## Reference\n\n- [CLI](cli.md)\n"
    )


def test_a_region_at_the_end_leaves_no_trailing_blank_lines() -> None:
    text = f"# Index\n\n- [Guide](guide.md)\n\n<!-- {BEGIN} -->\n\n## Plan\n\n<!-- {END} -->\n"
    assert strip_fences(text) == "# Index\n\n- [Guide](guide.md)\n"


def test_text_between_regions_is_kept_and_blank_lines_after_kept_text_stay() -> None:
    text = f"a\n# {BEGIN}\nx\n# {END}\n\nb\n# {BEGIN}\ny\n# {END}\nc\n"
    assert strip_fences(text) == "a\n\nb\nc\n"


@pytest.mark.parametrize(
    ("text", "line", "message"),
    [
        (f"# {BEGIN}\n# {BEGIN}\n# {END}\n", 2, "opens a fence inside the one opened on line 1"),
        (f"a\n# {END}\n", 2, "closes a fence that is not open"),
        (f"a\n\n# {BEGIN}\nb\n", 3, "opens a fence that is never closed"),
        ("a\nb\n", 1, f"has no '{BEGIN}' fence"),
    ],
)
def test_a_broken_fence_is_an_error(text: str, line: int, message: str) -> None:
    with pytest.raises(FenceError, match=message) as raised:
        strip_fences(text)
    assert raised.value.line == line
