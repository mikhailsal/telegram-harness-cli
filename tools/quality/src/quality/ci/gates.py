"""The CI gates over a candidate: workflow trust rules, actionlint, and the `[ci]` contract."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

from quality.adapters.actionlint import lint_workflows
from quality.adapters.native import ToolFailedError
from quality.adapters.settings import parse_yaml_mapping
from quality.boundedio import ReportError
from quality.ci.aggregate import check_required
from quality.ci.codeowners import CODEOWNERS, parse_codeowners, shadowing, unowned
from quality.ci.control import control_files
from quality.ci.gitlab import PATHS as GITLAB_PATHS
from quality.ci.gitlab import check_gitlab_documents
from quality.ci.workflow import SYNTAX, Table, check_workflow, finding, is_workflow
from quality.diagnostics import Diagnostic, Location
from quality.policy import CiPolicy, Policy
from quality.rules.model import CandidateFile


def _document(file: CandidateFile) -> Table | Diagnostic:
    if file.data is None:
        message = "the workflow is not a regular file within the blob size limit"
        return finding(SYNTAX, file.path, message, file.path)
    try:
        return parse_yaml_mapping(file.data, file.path)
    except ReportError as error:
        return finding(SYNTAX, file.path, str(error), file.path)


def _codeowners(
    files: Sequence[CandidateFile], policy: Policy, ci: CiPolicy
) -> Iterator[Diagnostic]:
    yield from shadowing(ci.codeowners, (file.path for file in files))
    found = [file for file in files if file.path == ci.codeowners]
    if not found:
        return
    text = found[0].text()
    if text is None:
        message = f"{ci.codeowners} is not a UTF-8 regular file within the blob size limit"
        yield CODEOWNERS.diagnostic(message, location=Location(ci.codeowners))
        return
    entries, problems = parse_codeowners(ci.codeowners, text)
    yield from problems
    control = control_files(policy, ci, (file.record for file in files))
    yield from unowned(ci.codeowners, entries, control)


def _gitlab(files: Sequence[CandidateFile]) -> Iterator[Diagnostic]:
    documents: dict[str, Table | None] = {}
    for file in files:
        if file.path in GITLAB_PATHS:
            document = _document(file)
            if isinstance(document, Diagnostic):
                documents[file.path] = None
                yield document
            else:
                documents[file.path] = document
    yield from check_gitlab_documents(documents)


def ci_gates(
    files: Sequence[CandidateFile], policy: Policy, root: Path, work: Path, *, actionlint: Path
) -> Iterator[Diagnostic]:
    """Check wrapper trust and lanes, plus the required workflow and ownership in `[ci]`."""
    yield from _gitlab(files)
    workflows = [file for file in files if is_workflow(file.path)]
    documents: dict[str, Table] = {}
    for file in workflows:
        document = _document(file)
        if isinstance(document, Diagnostic):
            yield document
            continue
        documents[file.path] = document
        yield from check_workflow(file.path, document)
    regular = [file.path for file in workflows if file.data is not None]
    try:
        yield from lint_workflows(regular, root, work, actionlint=actionlint)
    except ToolFailedError as error:
        yield error.diagnostic
    if policy.ci is not None:
        yield from check_required(documents.get(policy.ci.workflow), policy.ci)
        yield from _codeowners(files, policy, policy.ci)
