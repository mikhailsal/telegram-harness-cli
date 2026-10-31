"""`quality links`: probe the external links of the candidate's documentation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from quality.adapters.web import Probe
from quality.diagnostics import Rule
from quality.discovery import build_inventory
from quality.engine.check import Report
from quality.engine.common import candidate_files
from quality.engine.scope import checked_policy
from quality.gitio import Candidate, Repository, materialize
from quality.policy import Category
from quality.rules import exceptions, external
from quality.rules.links import is_markdown
from quality.rules.markdown import MarkdownFacts, parse_markdown
from quality.rules.model import CandidateFile

WORKERS = 8
LINK_RULES = frozenset(rule.id for rule in external.RULES)

type Prober = Callable[[str], Probe]


def documentation_pages(files: Iterable[CandidateFile]) -> list[tuple[str, MarkdownFacts]]:
    """Parse every UTF-8 Markdown page of the documentation category."""
    pages: list[tuple[str, MarkdownFacts]] = []
    for file in files:
        text = file.text()
        if file.category is Category.DOCS and is_markdown(file.path) and text is not None:
            pages.append((file.path, parse_markdown(text)))
    return pages


def check_links(
    repo: Repository,
    candidate: Candidate,
    *,
    rules: Mapping[str, Rule],
    prober: Prober,
    today: date,
) -> Report:
    """Probe each external link once, concurrently, and report broken and unverified ones."""
    with materialize(repo, candidate.tree) as snapshot:
        policy = checked_policy(snapshot.root, rules)
        if isinstance(policy, tuple):
            return Report(policy)
        inventory = build_inventory(policy, snapshot.root, snapshot.entries)
        limit = policy.thresholds.blob_bytes
        pages = documentation_pages(
            candidate_files(snapshot.root, snapshot.entries, inventory, limit)
        )
    urls = external.external_links(pages)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        probes = dict(zip(urls, pool.map(prober, urls), strict=True))
    results = {url: (found.status, found.detail) for url, found in probes.items()}
    applied = exceptions.apply_exceptions(
        external.judge(urls, results),
        [exception for exception in policy.exceptions if exception.rule in LINK_RULES],
        rules=rules,
        ran=exceptions.Ran(LINK_RULES),
        today=today,
    )
    return Report(applied.diagnostics, len(pages), applied.excepted)
