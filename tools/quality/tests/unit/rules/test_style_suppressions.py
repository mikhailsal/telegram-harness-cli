import pytest

from quality.policy import Category
from quality.rules.suppressions import check_suppressions
from quality.testkit.candidates import candidate

CSS = """\
/* stylelint-disable color-named */
a {
  color: red; /* stylelint-disable-line declaration-no-important */
}
/* prettier-ignore */
b{color:blue}
.c::after {
  content: "stylelint-disable";
}
"""
HTML = """\
<!doctype html>
<!-- prettier-ignore -->
<div   class="x"></div>
<p>prettier-ignore is not a marker outside a comment</p>
"""


def symbols(path: str, text: str, category: Category, styled: frozenset[str]) -> list[str]:
    file = candidate(path, text, category, component="web")
    return [
        f"{d.location.line if d.location else 0}: {d.data['symbol']}"
        for d in check_suppressions([file], styled)
    ]


@pytest.mark.parametrize("category", [Category.ASSETS, Category.TESTS])
def test_stylelint_and_prettier_markers_count_in_browser_stylesheets(category: Category) -> None:
    assert symbols("web/src/a.css", CSS, category, frozenset({"web"})) == [
        "1: stylelint-disable color-named",
        "3: stylelint-disable-line declaration-no-important",
        "5: prettier-ignore",
    ]


def test_prettier_markers_count_in_browser_html() -> None:
    assert symbols("web/index.html", HTML, Category.ASSETS, frozenset({"web"})) == [
        "2: prettier-ignore"
    ]


@pytest.mark.parametrize(("path", "text"), [("web/src/a.css", CSS), ("web/index.html", HTML)])
def test_stylesheets_and_html_of_other_components_carry_no_tool_markers(
    path: str, text: str
) -> None:
    assert symbols(path, text, Category.ASSETS, frozenset({"admin"})) == []
