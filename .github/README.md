# CI and repository policy

This policy follows Flightrepo, with checks adapted to BoboWatch's existing
standard-library unittest suite and supported Python versions.

## Request CI on a pull request

1. Rebase the branch onto the latest `main`. Development branches require linear
   history; do not merge `main` into them.
2. Add any label to request CI. Use `run-ci` by convention. If already present,
   remove it and add it again.
3. Wait for both Python 3.11 and 3.14 jobs and **Dashboard browser checks**. They
   run tests, lint, formatting and fictional desktop/phone workflows on Linux.
   The server-side required status remains `unittest (Python 3.11)` from GitHub
   Actions; the browser job must also pass the dashboard review checklist.
4. Merge using squash or rebase once the required check passes on the current,
   up-to-date branch. No reviewer approval is required by the ruleset.

Opening a PR or pushing another commit does not start CI. After every update,
add a label again. Any label triggers the complete workflow, including labels
with other purposes. There is no skip condition on the required job.

Every push to `main` automatically runs the same checks. Separate commit SHAs
have separate concurrency groups so newer updates do not cancel older runs.
A multi-commit push checks its final state. No monitoring process is deployed or
started by CI; HTTP test processes exist only for the duration of the tests.
No repository secrets are required.

## Branch protection and merge settings

- `main` requires a PR, an up-to-date branch and passing required CI.
- Force pushes and deletion of `main` are blocked. There are no bypass actors.
- Every other branch requires linear history.
- Squash and rebase merges are enabled; merge commits, auto-merge and the merge
  branch-update button are disabled. Merged development branches are deleted.
- Actions allows all actions, defaults to read-only workflow permissions and
  cannot approve pull requests. This workflow explicitly uses `contents: read`.

Writable settings are recorded in `repository-settings.json` and
`rulesets/{main,development}.json`. They are applied through GitHub's API, not
by this workflow. Local clones do not install these server-side rules.

References: [workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
and [required checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).


Calendar boundary and daylight-saving tests run with Node's built-in test runner
in the Python checks workflow. The hosted runner already includes Node; no npm
packages or application runtime dependencies are added.

The separate browser job installs locked development-only Playwright packages
from `tests/browser/`, runs Chromium and WebKit interaction checks, and deletes
its disposable fictional runtime afterward. Browser engines, Node and npm are
not added to the installed monitoring server or producer adapters. See
[local browser checks](../tests/browser/README.md).
