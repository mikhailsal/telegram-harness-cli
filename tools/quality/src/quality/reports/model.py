"""The evidence one suite run of one component leaves behind."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from quality.coverage import CoverageReport
from quality.testing import Suite, SuiteResults


@dataclass(frozen=True, slots=True)
class Evidence:
    """A suite run's normalized results and unit coverage, and the native reports it wrote.

    `results` is None when the run left no valid results; `native` are the
    runner's own report files, inside the run's output directory.
    """

    component: str
    suite: Suite
    results: SuiteResults | None
    coverage: CoverageReport | None = None
    native: tuple[Path, ...] = ()
