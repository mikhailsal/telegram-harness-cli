from pathlib import Path

from quality.adapters.typescript.entries import ENTRIES, PAGE_LIMIT, check_entries
from quality.diagnostics import Location
from quality.testkit.nodefakes import browser_component

SETTINGS = (
    "[components.browser]\n"
    'entries = ["index.html", "pages/about.html"]\n'
    'public_env = { VITE_ASSETS = "/assets" }'
)
WEB = browser_component(extra=SETTINGS)
FOREIGN = "loads from another origin; the page may load only its own files"
ENTRY_POINTS = ("web/index.html", "web/pages/about.html")
OWNED = frozenset(
    {
        *ENTRY_POINTS,
        "web/src/main.tsx",
        "web/src/app.css",
        "web/public/icon.svg",
        "web/public/assets/manifest.json",
        "web/pages/about.tsx",
        "web/src/my page.tsx",
    }
)
INDEX = """\
<!doctype html>
<html lang="en">
  <head>
    <link rel="icon" href="/icon.svg">
    <link rel="manifest" href="%VITE_ASSETS%/manifest.json">
    <link rel="Stylesheet preload" href="/src/app.css?inline#top">
    <link rel="canonical" href="https://example.test/">
    <link rel="preconnect" href="https://fonts.example.test">
    <link rel="icon" href="data:image/png;base64,AAAA">
    <script>console.log("inline")</script>
    <script type="module" src="/src/main.tsx"></script>
    <script src="/src/my%20page.tsx"></script>
  </head>
</html>
"""
ABOUT = """\
<html lang="en">
<script type="module" src="./about.tsx"></script>
<script src="../src/main.tsx"></script>
<script src="https://cdn.example.test/react.js"></script>
<script src="//cdn.example.test/dom.js"></script>
<link rel="stylesheet" href="%VITE_THEME%/theme.css">
<link rel="modulepreload" href="/src/missing.ts">
<link rel="stylesheet" href="">
<script src="main.tsx"></script>
</html>
"""


def check(snapshot: Path, owned: frozenset[str] = OWNED) -> list[tuple[Location | None, str]]:
    found = check_entries(WEB, snapshot, ENTRY_POINTS, owned)
    assert {(d.rule, d.component) for d in found} <= {(ENTRIES.id, "web")}
    return [(d.location, d.message) for d in found]


def write(snapshot: Path, index: str = INDEX, about: str = ABOUT) -> None:
    (snapshot / "web" / "pages").mkdir(parents=True)
    (snapshot / "web" / "index.html").write_text(index)
    (snapshot / "web" / "pages" / "about.html").write_text(about)


def test_references_resolve_the_way_vite_resolves_them(snapshot: Path) -> None:
    write(snapshot, about="<html></html>\n")
    assert check(snapshot) == []


def test_unresolved_foreign_and_undeclared_references_are_findings(snapshot: Path) -> None:
    write(snapshot)
    about = "web/pages/about.html"
    assert check(snapshot) == [
        (Location(about, 4), f"'https://cdn.example.test/react.js' {FOREIGN}"),
        (Location(about, 5), f"'//cdn.example.test/dom.js' {FOREIGN}"),
        (
            Location(about, 6),
            "'%VITE_THEME%/theme.css' names VITE_THEME, which public_env does not declare",
        ),
        (Location(about, 7), "'/src/missing.ts' does not resolve to a file of the component"),
        (Location(about, 8), "'' does not resolve to a file of the component"),
        (Location(about, 9), "'main.tsx' does not resolve to a file of the component"),
    ]


def test_a_missing_entry_point_is_a_finding(snapshot: Path) -> None:
    write(snapshot, about="<html></html>\n")
    assert check(snapshot, OWNED - {"web/index.html"}) == [
        (None, "the declared entry point web/index.html is not a file of the candidate")
    ]


def test_an_unreadable_entry_point_is_a_finding(snapshot: Path) -> None:
    write(snapshot, about="<html></html>\n")
    (snapshot / "web" / "index.html").write_bytes(b" " * (PAGE_LIMIT + 1))
    (snapshot / "web" / "pages" / "about.html").unlink()
    (snapshot / "web" / "pages" / "about.html").symlink_to(snapshot / "web" / "index.html")
    message = "the entry point is not a regular file of at most 4 MiB"
    assert check(snapshot) == [
        (Location("web/index.html"), message),
        (Location("web/pages/about.html"), message),
    ]
