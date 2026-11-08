# Hosting protection

The gate can only fail a status. Whether a failed status blocks a merge is a
setting of the hosting service, outside the repository. This guide lists what
the repository prepares, how an owner applies it, how to confirm it is active,
and what it cannot protect against. The mechanics are in
[decision 0015](../decisions/0015-ci-wrappers-and-merge-gates.md).

## What the repository prepares

| File | Role |
| --- | --- |
| [`.github/workflows/quality.yml`](../../.github/workflows/quality.yml) | Runs both lanes of `gate`, in the jobs `gate` and `browser`, and `audit`; its job `required` is the one status to require |
| [`.github/CODEOWNERS`](../../.github/CODEOWNERS) | Owners of every file, with the control files listed last |
| [`.github/rulesets/main.json`](../../.github/rulesets/main.json) | A branch ruleset for the default branch, ready to import |
| [`quality.toml`](../../quality.toml) `[ci]` | The workflow, the aggregate job, and the jobs it requires |
| [`ci/gitlab/quality.gitlab-ci.yml`](../../ci/gitlab/quality.gitlab-ci.yml) | The GitLab reference wrapper |

`quality check` keeps these consistent: the workflow must satisfy the `ci.*`
rules, and every control file needs a code owner. A browser component's
build, test, lint, and TypeScript configuration, its package manifest and
lockfile, and its scenario manifest are control files too. A derived repository
replaces `@mikhailsal` in CODEOWNERS with its own users or teams.

## The ruleset

`main.json` targets the default branch and has no bypass actors:

- `deletion` and `non_fast_forward`: the branch cannot be deleted or
  rewritten;
- `pull_request`: changes arrive only through a pull request with one
  approval, a code-owner review, stale approvals dismissed on push, approval
  of the last push by someone other than its author, and resolved threads;
- `required_status_checks`: the status `required`, accepted only from the
  GitHub Actions app (integration 15368), so a person or another app with
  write access cannot set it; the branch must be up to date before merging,
  so the tested merge is the merge that lands.

Apply it as the repository owner, either in Settings → Rules → Rulesets →
Import a ruleset, or with the GitHub CLI:

```sh
gh api --method POST repos/OWNER/REPO/rulesets --input .github/rulesets/main.json
```

Also review the Actions settings: keep the default workflow token read-only,
do not let GitHub Actions approve pull requests, and require approval before
workflows from outside contributors run.

### Choices left to the owner

- A sole maintainer cannot approve their own pull requests. Either add a
  second maintainer, or add the repository admin role as a bypass actor with
  `"bypass_mode": "pull_request"`, which still forces a pull request and
  records every bypass. A third way keeps the status enforced for everyone
  and drops review: apply the ruleset with `required_approving_review_count`
  0 and `require_code_owner_review` and `require_last_push_approval` false.
  Each way is a decision to make deliberately.
- A merge queue (organization repositories) needs a `merge_queue` rule; the
  workflow already handles `merge_group` events. Without a queue, the
  up-to-date requirement does the same job, one pull request at a time.

## Confirming the protection

A ruleset in the repository is a proposal until the host applies it. Confirm
with the host's own view of the branch:

```sh
gh api repos/OWNER/REPO/rules/branches/main
gh api repos/OWNER/REPO/rulesets
```

Then prove it: a direct push to `main` must be rejected, and a pull request
whose `gate` or `browser` job fails must show `required` as failed and the
merge button blocked. Until someone has observed this, record the protection as
unverified.

## GitLab

Protect the default branch with "Allowed to merge" set to Maintainers and
"Allowed to push and merge" set to No one, enable "Pipelines must succeed",
and turn on code-owner approval for the protected branch. GitLab reads CODEOWNERS from
the root, `docs/`, or `.gitlab/`; set `[ci].codeowners` to `CODEOWNERS` or
`docs/CODEOWNERS`, which both hosts read. The pipeline status is the required
status there, so the wrapper has no aggregate job.

## The real boundary

- Local hooks are a convenience. `git commit --no-verify`, a removed hook, or
  a different clone skips them; only the hosted status counts.
- The gate runs the candidate's own core, so a pull request can change the
  validator, the policy, and the workflow it is judged by. The `ci.*` rules
  catch a weakened workflow, `ci.control-changed` lists every control change,
  and code-owner review is what stops a bad one. A reviewer who approves a
  control change approves what the gate will check from then on.
- Repository files cannot constrain an equally privileged administrator. An
  admin can edit or disable the ruleset, add themselves as a bypass actor, or
  change the Actions settings, and the repository cannot detect it. Audit
  logs and a second owner are the defences, not this template.
- Required review is only as strong as the reviewers. CODEOWNERS names who
  must review, not how carefully.

A stronger option for later is a trusted external validator: a required
status set by a workflow or app that the candidate cannot change, which runs
a pinned core from a protected repository against the candidate. Version 1
does not build one.
