"""Trust rules for GitHub Actions workflows that a schema linter does not apply (R09)."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import cast

from quality.diagnostics import Diagnostic, Location, Rule

type Table = Mapping[str, object]

SYNTAX = Rule(
    id="ci.workflow-syntax",
    title="Workflow is not a readable YAML mapping",
    requirement="R09",
    explanation=(
        "A workflow under .github/workflows must be a regular file holding one YAML mapping "
        "without repeated keys, so the trust rules see what GitHub runs."
    ),
    remedy="Fix the YAML; actionlint reports the exact position.",
)
UNPINNED = Rule(
    id="ci.unpinned-action",
    title="Workflow action is not pinned to a commit",
    requirement="R09",
    explanation=(
        "A tag or branch of an action can be moved to different code after review. Actions "
        "and reusable workflows are referenced by a full 40-character commit SHA, Docker "
        "images by a sha256 digest; local `./` references belong to the candidate itself."
    ),
    remedy="Reference `owner/repo@<full commit SHA>` and keep the tag in a comment.",
)
PRIVILEGED = Rule(
    id="ci.privileged-trigger",
    title="Workflow uses a privileged trigger",
    requirement="R09",
    explanation=(
        "`pull_request_target` and `workflow_run` run with the base repository's secrets and "
        "a token that can write, while the code under test may come from an untrusted pull "
        "request. The gate never runs candidate code in such a context."
    ),
    remedy="Use `pull_request` and `merge_group`; publish results without a privileged workflow.",
)
PERMISSIONS = Rule(
    id="ci.permissions",
    title="Workflow token permissions are not minimal",
    requirement="R09",
    explanation=(
        "A workflow sets `permissions: {}` at the top so no job inherits the repository "
        "default, and each job grants only read scopes. `read-all`, `write-all`, and any "
        "write scope are reported; the symbol names the job and the scope."
    ),
    remedy="Grant `contents: read` per job; move a job that must write into a reviewed workflow.",
)
CREDENTIALS = Rule(
    id="ci.persisted-credentials",
    title="Checkout leaves the token in the Git configuration",
    requirement="R09",
    explanation=(
        "actions/checkout stores the job token in .git/config unless `persist-credentials` "
        "is false, where every later step, including candidate code, can read it."
    ),
    remedy="Add `with: {persist-credentials: false}` to the checkout step.",
)
CONTINUES = Rule(
    id="ci.continue-on-error",
    title="Workflow job or step may fail without failing",
    requirement="R09",
    explanation=(
        "`continue-on-error` turns a failure into success for the job or step, so a "
        "required result could be green although its check failed."
    ),
    remedy="Remove `continue-on-error`; an optional check belongs in a job that is not required.",
)
SECRET = Rule(
    id="ci.secret",
    title="Workflow that runs candidate code reads secrets",
    requirement="R09",
    explanation=(
        "Workflows that run on `pull_request` or `merge_group` execute code that is not yet "
        "merged. Apart from the job's own GITHUB_TOKEN they must not read secrets, pass "
        "them to reusable workflows, or inherit them."
    ),
    remedy="Move the step that needs the secret to a workflow that runs only on merged code.",
)
RULES = (SYNTAX, UNPINNED, PRIVILEGED, PERMISSIONS, CREDENTIALS, CONTINUES, SECRET)

WORKFLOW_PATH = re.compile(r"\.github/workflows/[^/]+\.ya?ml")
PINNED = re.compile(r"[\w.-]+/[\w.-]+(?:/[^@\s]+)?@[0-9a-f]{40}")
DIGEST = re.compile(r"docker://[^@\s]+@sha256:[0-9a-f]{64}")
EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
SECRET_USE = re.compile(r"\bsecrets\b(?!\s*\.\s*GITHUB_TOKEN\b)")
PRIVILEGED_EVENTS = ("pull_request_target", "workflow_run")
UNTRUSTED_EVENTS = ("pull_request", "merge_group")
CHECKOUT = "actions/checkout@"
FALSE = (False, "false")


def is_workflow(path: str) -> bool:
    """Whether GitHub reads the path as a workflow."""
    return WORKFLOW_PATH.fullmatch(path) is not None


def mapping(value: object) -> Table | None:
    """Return a YAML mapping, or None for other values."""
    return cast(Table, value) if isinstance(value, Mapping) else None


def items(value: object) -> list[object]:
    """Return the items of a YAML sequence; other values hold none."""
    return cast(list[object], value) if isinstance(value, list) else []


def events(document: Table) -> dict[str, object]:
    """Return the triggers by event name; YAML 1.1 reads the key `on` as true."""
    keys = cast(Mapping[object, object], document)
    value = keys.get("on", keys.get(True))
    if isinstance(value, str):
        return {value: None}
    table = mapping(value)
    if table is not None:
        return {str(name): settings for name, settings in table.items()}
    return {item: None for item in items(value) if isinstance(item, str)}


def jobs(document: Table) -> dict[str, Table]:
    """Return the jobs that are mappings, by ID."""
    table = mapping(document.get("jobs")) or {}
    found = {str(name): mapping(job) for name, job in table.items()}
    return {name: job for name, job in found.items() if job is not None}


def steps(job: Table) -> list[Table]:
    """Return the steps of a job that are mappings."""
    found = [mapping(step) for step in items(job.get("steps"))]
    return [step for step in found if step is not None]


def finding(rule: Rule, path: str, message: str, symbol: str) -> Diagnostic:
    """Report a workflow finding; the symbol makes an exception precise."""
    return rule.diagnostic(message, location=Location(path), data={"symbol": symbol})


def pinned(reference: str) -> bool:
    """Whether a `uses` reference cannot change after review."""
    if reference.startswith("./"):
        return True
    return PINNED.fullmatch(reference) is not None or DIGEST.fullmatch(reference) is not None


def _references(document: Table) -> Iterator[tuple[str, str]]:
    for name, job in jobs(document).items():
        value = job.get("uses")
        if isinstance(value, str):
            yield f"job '{name}'", value
        for number, step in enumerate(steps(job), start=1):
            value = step.get("uses")
            if isinstance(value, str):
                yield f"job '{name}' step {number}", value


def _grants(value: object) -> Iterator[tuple[str, str]]:
    """Yield each scope a permissions value grants beyond reading one scope."""
    if isinstance(value, str):
        yield value, value
    for scope, level in (mapping(value) or {}).items():
        if level == "write":
            yield str(scope), f"{scope}: write"


def _permissions(path: str, document: Table) -> Iterator[Diagnostic]:
    if "permissions" not in document:
        message = "the workflow does not set top-level permissions"
        yield finding(PERMISSIONS, path, message, "workflow")
    for scope, grant in _grants(document.get("permissions")):
        yield finding(PERMISSIONS, path, f"the workflow grants '{grant}'", f"workflow:{scope}")
    for name, job in jobs(document).items():
        for scope, grant in _grants(job.get("permissions")):
            yield finding(PERMISSIONS, path, f"job '{name}' grants '{grant}'", f"{name}:{scope}")


def _steps(path: str, document: Table) -> Iterator[Diagnostic]:
    for name, job in jobs(document).items():
        if job.get("continue-on-error", False) not in FALSE:
            yield finding(CONTINUES, path, f"job '{name}' sets continue-on-error", name)
        for number, step in enumerate(steps(job), start=1):
            where = f"job '{name}' step {number}"
            if step.get("continue-on-error", False) not in FALSE:
                yield finding(
                    CONTINUES, path, f"{where} sets continue-on-error", f"{name}:{number}"
                )
            uses = step.get("uses")
            options = mapping(step.get("with")) or {}
            checkout = isinstance(uses, str) and uses.startswith(CHECKOUT)
            if checkout and options.get("persist-credentials") not in FALSE:
                message = f"{where} checks out without 'persist-credentials: false'"
                yield finding(CREDENTIALS, path, message, f"{name}:{number}")


def _strings(value: object, key: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield key, value
    for name, item in (mapping(value) or {}).items():
        yield from _strings(item, str(name))
    for item in items(value):
        yield from _strings(item, key)


def secret_uses(document: Table) -> list[str]:
    """Return every expression that reads a secret other than GITHUB_TOKEN, and `inherit`."""
    found: list[str] = []
    for key, text in _strings(document):
        expressions = [*EXPRESSION.findall(text), *([text] if key == "if" else [])]
        found.extend(e.strip() for e in expressions if SECRET_USE.search(e))
    if any(job.get("secrets") == "inherit" for job in jobs(document).values()):
        found.append("secrets: inherit")
    return list(dict.fromkeys(found))


def check_workflow(path: str, document: Table) -> Iterator[Diagnostic]:
    """Apply the trust rules to one parsed workflow."""
    triggers = events(document)
    for event in PRIVILEGED_EVENTS:
        if event in triggers:
            yield finding(PRIVILEGED, path, f"the workflow runs on '{event}'", event)
    for where, reference in _references(document):
        if not pinned(reference):
            message = f"{where} uses '{reference}', which is not pinned to a commit"
            yield finding(UNPINNED, path, message, reference)
    yield from _permissions(path, document)
    yield from _steps(path, document)
    untrusted = [event for event in UNTRUSTED_EVENTS if event in triggers]
    if untrusted:
        for use in secret_uses(document):
            message = f"the workflow runs on '{untrusted[0]}' and reads '{use}'"
            yield finding(SECRET, path, message, use)
