"""Reports a gate run leaves for CI: JUnit, LCOV, Cobertura, and native reports (R09)."""

from quality.reports.cobertura import cobertura_document
from quality.reports.junit import junit_document
from quality.reports.lcov import lcov_document
from quality.reports.model import Evidence
from quality.reports.write import RULES, ReportsError, prepare_reports, write_evidence

__all__ = [
    "RULES",
    "Evidence",
    "ReportsError",
    "cobertura_document",
    "junit_document",
    "lcov_document",
    "prepare_reports",
    "write_evidence",
]
