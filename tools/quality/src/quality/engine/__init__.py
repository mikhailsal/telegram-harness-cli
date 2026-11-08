"""Orchestration: run the gates and the test suites over a candidate."""

from quality.engine import audit, check, gate, provenance, suites
from quality.engine.check import Report, Settings, check_candidate
from quality.engine.common import Tools
from quality.engine.history import check_history
from quality.engine.links import check_links
from quality.engine.profiles import ProfileAdapter, ProfileSetup, ProfileTests
from quality.engine.suites import SuiteReport, SuiteSettings, run_suites

RULES = (*audit.RULES, *check.RULES, *gate.RULES, *suites.RULES, *provenance.RULES)

__all__ = [
    "RULES",
    "ProfileAdapter",
    "ProfileSetup",
    "ProfileTests",
    "Report",
    "Settings",
    "SuiteReport",
    "SuiteSettings",
    "Tools",
    "check_candidate",
    "check_history",
    "check_links",
    "run_suites",
]
