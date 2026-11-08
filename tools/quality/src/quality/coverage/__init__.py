"""Coverage: the normalized model and parsers of native coverage reports (no subprocesses)."""

from quality.coverage.lcov import parse_lcov
from quality.coverage.model import EMPTY, Counter, CoverageReport, FileCoverage, total
from quality.coverage.paths import repository_path
from quality.coverage.phpunit import JsonlReport, parse_phpunit
from quality.coverage.python import parse_coverage_json

__all__ = [
    "EMPTY",
    "Counter",
    "CoverageReport",
    "FileCoverage",
    "JsonlReport",
    "parse_coverage_json",
    "parse_lcov",
    "parse_phpunit",
    "repository_path",
    "total",
]
