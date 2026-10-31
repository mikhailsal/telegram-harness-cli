import hashlib
from pathlib import Path

import pytest

from quality.adapters.native import ToolFailedError
from quality.adapters.typescript import bundle
from quality.adapters.typescript.bundle import Output, check_output, collect, problem
from quality.testkit.nodefakes import browser_component

PAGE = """\
<!doctype html>
<html lang="en">
  <head>
    <link rel="icon" href="/icon.svg" />
    <link rel="stylesheet" href="https://cdn.example.com/a.css" />
    <script type="module" src="/assets/index.js"></script>
    <script type="module" src="/assets/gone.js"></script>
  </head>
</html>
"""
STYLES = """\
.a { background: url("/icon.svg"), url('../logo.png'), url(fonts/x.woff2?v=1#y); }
.b { mask: url(#clip); background: url(data:image/png;base64,AAAA), url(/missing.png); }
"""


def write(directory: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (directory / path).parent.mkdir(parents=True, exist_ok=True)
        (directory / path).write_text(text)


def test_the_evidence_hashes_every_output_file(tmp_path: Path) -> None:
    write(tmp_path, {"index.html": "<p>a</p>", "assets/a.js": "1", ".vite/manifest.json": "{}"})
    (tmp_path / "link.js").symlink_to(tmp_path / "assets/a.js")
    output = collect(tmp_path, "tree-1")
    assert sorted(output.files) == [".vite/manifest.json", "assets/a.js", "index.html"]
    assert output.files["assets/a.js"] == hashlib.sha256(b"1").hexdigest()
    listing = "".join(f"{output.files[path]}  {path}\n" for path in sorted(output.files))
    assert output.digest == hashlib.sha256(listing.encode()).hexdigest()
    assert output.evidence() == {
        "candidate": "tree-1",
        "digest": output.digest,
        "files": dict(sorted(output.files.items())),
    }


def test_a_missing_output_directory_holds_no_files(tmp_path: Path) -> None:
    assert collect(tmp_path / "dist", "tree").files == {}


def test_built_pages_and_stylesheets_reference_only_output_files(tmp_path: Path) -> None:
    write(
        tmp_path,
        {
            "index.html": PAGE,
            "icon.svg": "<svg/>",
            "assets/index.js": "",
            "assets/index.css": STYLES,
            "logo.png": "",
            "assets/fonts/x.woff2": "",
            "public.html": '<script src="/nowhere.js"></script>',
        },
    )
    found = check_output(browser_component(), collect(tmp_path, "tree"), "/")
    assert [diagnostic.message for diagnostic in found] == [
        "index.html: 'https://cdn.example.com/a.css' loads from another origin",
        "index.html: '/assets/gone.js' is not a file of the build",
        "assets/index.css: '/missing.png' is not a file of the build",
    ]
    assert {diagnostic.rule for diagnostic in found} == {"typescript.build-output"}


def test_every_declared_entry_point_must_be_built(tmp_path: Path) -> None:
    component = browser_component(extra='[components.browser]\nentries = ["a.html", "b.html"]')
    write(tmp_path, {"a.html": "<p>a</p>"})
    found = check_output(component, collect(tmp_path, "tree"), "/")
    assert [diagnostic.message for diagnostic in found] == ["the build wrote no b.html"]


def test_urls_resolve_below_an_absolute_or_relative_base(tmp_path: Path) -> None:
    output = Output(tmp_path, "tree", {"assets/a.js": "", "pages/b.html": ""})
    assert problem(output, "index.html", "/app/assets/a.js", "/app/") is None
    assert problem(output, "index.html", "/assets/a.js", "/app/") is not None
    assert problem(output, "pages/b.html", "../assets/a.js", "./") is None
    assert problem(output, "pages/b.html", "./assets/a.js", "./") is not None
    assert problem(output, "index.html", "//cdn.example.com/a.js", "/") is not None


def test_an_unreadable_built_file_is_a_tool_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, {"index.html": "<p>large</p>"})
    monkeypatch.setattr(bundle, "READ_LIMIT", 4)
    with pytest.raises(ToolFailedError, match=r"the built index\.html cannot be read"):
        check_output(browser_component(), collect(tmp_path, "tree"), "/")
