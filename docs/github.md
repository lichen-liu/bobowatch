# GitHub repository

BoboWatch has its own public repository:
<https://github.com/lichen-liu/bobowatch>.
The independent local checkout is `~/p/bobowatch`, on branch `main`.

```sh
git clone https://github.com/lichen-liu/bobowatch.git
cd bobowatch
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-core.txt
.venv/bin/python -m bobowatch demo
```

## Development and CI

Create a development branch, make logical commits and open a pull request.
Add the `run-ci` label to request CI, then request it again after any new commits
or rebase. Checks run automatically after every push to `main`.
See [the CI and branch policy](../.github/README.md) for exact required checks
and merge rules. No repository secrets are needed for BoboWatch's own CI or for
other projects to fetch this public repository as a submodule.

## Source and runtime data

Commit source, SQL migrations, examples, documentation, screenshot and tests.
Keep runtime configuration, real tokens, SQLite files, backups and logs outside
the source checkout. Ignore rules also exclude common accidental runtime files.
The example inventory contains file references rather than real credentials.

This repository does not deploy or start a monitoring process. Run BoboWatch
manually when monitoring is wanted, as described in the README. This milestone
is a prototype with a fictional demo; real application integrations remain future
work. Consuming projects should pin a reviewed Git submodule commit.

No open-source license has been selected yet. Public visibility makes the source
readable; it does not by itself grant an open-source license.
