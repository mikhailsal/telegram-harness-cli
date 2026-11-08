import json
from pathlib import Path

import pytest

from quality.discovery.frontend import Evidence, inspect
from quality.discovery.manifests import MAX_MANIFEST_BYTES, ManifestError

STACK = {"react": "19.2.6", "react-dom": "19.2.6"}
TOOLS = {"vite": "8.3.5", "@vitejs/plugin-react": "6.1.2"}


def project(tmp_path: Path, manifest: object, *files: str) -> Path:
    (tmp_path / "package.json").write_text(json.dumps(manifest))
    for name in files:
        (tmp_path / name).write_text("")
    return tmp_path


def test_a_react_vite_application_is_complete_evidence(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        {"dependencies": STACK, "devDependencies": TOOLS},
        "vite.config.ts",
        "index.html",
    )
    evidence = inspect(root)
    assert evidence == Evidence(
        packages=frozenset({*STACK, *TOOLS}),
        vite_config="vite.config.ts",
        entry=True,
        unsupported=(),
    )
    assert evidence.missing == ()
    assert evidence.found == (
        "react",
        "react-dom",
        "vite",
        "@vitejs/plugin-react",
        "vite.config.ts",
        "index.html",
    )


def test_a_package_json_or_tsx_alone_is_no_evidence(tmp_path: Path) -> None:
    root = project(
        tmp_path, {"name": "cli", "dependencies": {"commander": "1.0.0"}}, "index.html", "a.tsx"
    )
    evidence = inspect(root)
    assert evidence.found == ()
    assert evidence.missing == (
        "the react package",
        "the react-dom package",
        "the vite package",
        "the @vitejs/plugin-react package",
        "a vite.config.* file",
    )
    assert inspect(tmp_path / "absent").found == ()


def test_partial_evidence_and_other_frameworks_are_named(tmp_path: Path) -> None:
    root = project(tmp_path, {"optionalDependencies": {"react": "19"}}, "vite.config.mjs")
    evidence = inspect(root)
    assert evidence.found == ("react", "vite.config.mjs")
    assert "an index.html entry point" in evidence.missing
    (tmp_path / "next").mkdir()
    other = project(tmp_path / "next", {"dependencies": {**STACK, "next": "16", "vue": "3"}})
    assert inspect(other).unsupported == ("Next.js", "Vue")
    assert inspect(other).found == ("react", "react-dom", "Next.js", "Vue")


@pytest.mark.parametrize("data", [b"{", b"\xff", b"[" * 10_000, b" " * (MAX_MANIFEST_BYTES + 1)])
def test_an_unreadable_manifest_is_an_error(tmp_path: Path, data: bytes) -> None:
    (tmp_path / "package.json").write_bytes(data)
    with pytest.raises(ManifestError, match=r"package\.json"):
        inspect(tmp_path)


def test_a_manifest_that_is_not_an_object_has_no_dependencies(tmp_path: Path) -> None:
    root = project(tmp_path, ["react"], "vite.config.js")
    assert inspect(root).found == ("vite.config.js",)
    assert inspect(project(tmp_path, {"dependencies": ["react"]})).packages == frozenset()
