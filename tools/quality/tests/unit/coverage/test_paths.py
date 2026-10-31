from pathlib import Path

import pytest

from quality.boundedio import ReportError
from quality.coverage import repository_path

ROOT = Path("/work/snapshot")


@pytest.mark.parametrize(
    ("raw", "cwd", "expected"),
    [
        ("pkg/m.py", ROOT, "pkg/m.py"),
        ("./pkg/../pkg/m.py", ROOT, "pkg/m.py"),
        ("src/m.ts", ROOT / "web", "web/src/m.ts"),
        ("../shared/x.ts", ROOT / "web", "shared/x.ts"),
        ("/work/snapshot/src/Calc.php", ROOT / "api", "src/Calc.php"),
    ],
)
def test_reported_names_become_repository_paths(raw: str, cwd: Path, expected: str) -> None:
    assert repository_path(raw, root=ROOT, cwd=cwd) == expected


def test_the_resolved_snapshot_path_is_accepted(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    root = tmp_path / "link"
    root.symlink_to(real)
    assert repository_path(str(real / "a.py"), root=root, cwd=root) == "a.py"
    assert repository_path(str(root / "a.py"), root=root, cwd=root) == "a.py"


@pytest.mark.parametrize(
    "raw",
    ["../outside.py", "/etc/passwd", "/work/snapshot-other/a.py", ".", "/work/snapshot"],
)
def test_names_outside_the_snapshot_are_rejected(raw: str) -> None:
    with pytest.raises(ReportError, match=r"^report names a file outside the snapshot: "):
        repository_path(raw, root=ROOT, cwd=ROOT)


@pytest.mark.parametrize("raw", ["", "a\0b.py"])
def test_empty_and_nul_names_are_rejected(raw: str) -> None:
    with pytest.raises(ReportError, match=r"^invalid file name in report: "):
        repository_path(raw, root=ROOT, cwd=ROOT)
