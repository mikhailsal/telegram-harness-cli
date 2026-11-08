# 0015 CI wrappers and merge gates

Status: accepted (P10); amended in F02 and F06.

## Scope

The CLI decides every pass or fail. A CI wrapper only translates its
provider's event into explicit revisions, installs the pinned environment,
calls the CLI, and keeps the reports. The commands it calls, `quality gate`,
`quality audit`, and `quality ci verdict`, read no provider variables, so the
same commands run on a plain Linux machine with a clone and nothing else.

The gate is in `quality/engine/gate.py` and `quality/cli/gate_cmd.py`, the
wrapper rules in `quality/ci/`, the audit in `quality/engine/audit.py` and
`quality/adapters/osv.py`, and the reports in `quality/reports/`.

## The gate

`quality gate --stage commit|merge` checks every active component of one
candidate, never just the affected ones:

| Candidate | Arguments |
| --- | --- |
| The index (a hook, or `--index FILE`) | `--stage commit` |
| A pushed range | `--stage commit --base BEFORE --head AFTER` |
| A pull request | `--stage merge --base B --head H [--tested T]` |
| A merge-queue entry | `--stage merge --base B --head T --queue` |

Line semantics, merge bases, and the zero revision of a new branch follow
[decision 0004](0004-candidate-snapshots.md#merge-and-pull-request-revisions).
A range also scans every blob of its new commits for secrets and for the blob
size limit. History that is shallow or lacks the base fails with
`git.revision-unresolvable` instead of shrinking the check, so CI checks out
with full history. Arguments that do not describe one candidate fail with
`gate.arguments` (exit 2).

`--lane main|browser` checks one part of the same candidate, so that CI can
run the browser components in a job of their own: the main lane runs the
whole-candidate gates and the other components, the browser lane the
browser components, and together they are the gate without `--lane`
([decision 0018](0018-browser-builds-and-scenarios.md#lanes)).

`--reports DIR` writes into a new or empty directory: `report.json` (the JSON
report with the candidate's revisions and trees), JUnit per suite, LCOV and
Cobertura per unit suite, and copies of the native reports with
repository-relative paths. Exports are written only from valid results. A
report that cannot be written fails with `reports.incomplete`; an uploaded
report is a record of the run, never its verdict.

`--baseline FILE` compares unit coverage with an earlier `report.json`.
A drop is a `coverage.regression` warning and an unreadable baseline a
`coverage.baseline` warning. The floors in `quality.toml` decide; there is no
ratchet.

## Event translation

| Provider event | Gate arguments |
| --- | --- |
| GitHub `pull_request` | `--stage merge --base <first parent of github.sha> --head <pull_request.head.sha> --tested <github.sha>` |
| GitHub `merge_group` | `--stage merge --base <merge_group.base_sha> --head <merge_group.head_sha> --queue` |
| GitHub `push` to main | `--stage commit --base <before> --head <github.sha>` |
| GitLab merged-results pipeline | `--stage merge --base <TARGET_BRANCH_SHA> --head <SOURCE_BRANCH_SHA> --tested <CI_COMMIT_SHA>` |
| GitLab merge request pipeline | `--stage merge --base <DIFF_BASE_SHA> --head <CI_COMMIT_SHA>` |
| GitLab merge train | `--stage merge --base <TARGET_BRANCH_SHA> --head <CI_COMMIT_SHA> --queue` |
| GitLab push to the default branch | `--stage commit --base <CI_COMMIT_BEFORE_SHA> --head <CI_COMMIT_SHA>` |

GitHub's tested merge commit is built on its first parent, which is used as
the base rather than the event's `base.sha`, which can be older. The
[GitHub workflow](../../.github/workflows/quality.yml) is the wrapper of this
repository; the
[GitLab wrapper](../../ci/gitlab/quality.gitlab-ci.yml) is a reference for
migration that only a live GitLab run could prove.

## The required status

Branch protection requires exactly one status: the aggregate job named in
`[ci].aggregate_job`. It runs `if: always()`, needs exactly
`[ci].required_jobs`, and passes `toJSON(needs)` to `quality ci verdict`.
The verdict fails unless every required job has the result `success`:
failed, cancelled, and skipped jobs fail (`ci.job-failed`), a required job
without a result fails (`ci.job-missing`), and a result the policy does not
expect fails (`ci.job-unexpected`). Unreadable results or a revision without
`[ci]` are usage errors (`ci.results`).

The gate checks the workflow that the candidate itself contains, so a pull
request cannot weaken the aggregate without the gate noticing:

| Rule | What it rejects |
| --- | --- |
| `ci.lanes` | Required jobs that run one lane of the gate but no required job that runs the other |
| `ci.aggregate` | The required workflow missing, not run on `pull_request` and `merge_group`, an aggregate without `if: always()`, with other needs, without the verdict or `toJSON(needs)`, or a required job with an `if` |
| `ci.path-filter` | `paths` or `paths-ignore` on any trigger, which would let a change skip the status |
| `ci.unpinned-action` | An action or reusable workflow not pinned to a 40-character commit SHA, or an image without a digest |
| `ci.privileged-trigger` | `pull_request_target` and `workflow_run` |
| `ci.permissions` | No top-level `permissions`, `read-all`, `write-all`, or any write scope |
| `ci.persisted-credentials` | `actions/checkout` without `persist-credentials: false` |
| `ci.continue-on-error` | `continue-on-error` on a job or step |
| `ci.secret` | Secrets other than `GITHUB_TOKEN`, or `secrets: inherit`, in a workflow that runs candidate code |
| `ci.actionlint` | What the pinned actionlint reports: syntax, schema, expressions, job dependencies, runner labels, and action inputs |

actionlint runs with an empty configuration owned by the core; a
`.github/actionlint.yaml` in the candidate is ignored. It is a default tool
of `quality bootstrap`, because every check lints the workflows.

Caches never carry trust from a pull request: jobs restore the cache of the
default branch, and only a push to the default branch saves it. The coverage
baseline of each lane is the `report.json` saved by that push.

Every job installs PHP 8.4, the one runtime that bootstrap does not download
([decision 0002](0002-installation-strategy.md)), and runs `quality
bootstrap`, because only bootstrap downloads, including the core's uv
([decision 0010](0010-launcher-bootstrap-and-hooks.md#launcher)). The jobs
of both lanes also lift the AppArmor restriction of Ubuntu 24.04 on
unprivileged user namespaces; without it the suites run with the network and the gate warns
([decision 0008](0008-test-and-coverage-engine.md#network-isolation)).

## Dependency gates

Lockfile consistency is part of every check: `uv lock --check --offline`
before a Python environment is synced, `npm ci`, `dart pub get
--enforce-lockfile`, and `composer validate --check-lock`. A manifest that no
longer matches its lockfile fails the component.

`quality audit` is the vulnerability gate of merge CI and not of `check`:
current advisories need the network. It scans every lockfile classified as
configuration (`uv.lock`, `package-lock.json`, `pubspec.lock`,
`composer.lock`) with the pinned osv-scanner, which asks the osv.dev API, or
matches offline with `--offline-db DIR`. The `[audit]` table is the severity
policy:

- an advisory whose highest CVSS score is at least `blocking_score` is an
  error, a lower one a warning (`dependencies.vulnerable`);
- an advisory without a score follows `unscored`, an error by default;
- a scanner, network, or database failure is `dependencies.scanner`, an
  environment error (exit 3), never a clean result.

Each finding's symbol is the first advisory ID, so an `[[exceptions]]` entry
covers exactly one advisory in one lockfile.

osv-scanner is a default tool of `quality bootstrap`, because the core's own
tests run it against offline databases.

## Control files

Control files decide what the gate checks: `quality.toml`, the launcher,
`.gitattributes`, everything under `.github`, `.gitlab-ci.yml`, the roots of
tooling and helper components (the core, its hooks, bootstrap, and validator)
and of example components, scenario manifests, native configuration, and the globs in `[ci].control`.
`ci.codeowners` requires an owner for each one in `[ci].codeowners`, including
the CODEOWNERS file itself, and reports the lines GitHub would ignore and a
CODEOWNERS file that GitHub reads instead. At the merge stage
`ci.control-changed` lists every changed control file as information: the
gate makes the change visible, and the hosting rule that requires code-owner
review is what protects it.

## Trust boundary

The protection is only as strong as the hosting settings and the people who
can change them; the [protection guide](../guides/protection.md) states the
boundary and the settings this repository prepares.

## Non-goals

No hosted policy service, no external validator, and no claim that server
protection is active until it has been observed on the host.
