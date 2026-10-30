# Dashboard browser checks

These checks use development-only Playwright, a disposable SQLite database and
fictional observations. They never connect to an installed BoboWatch runtime.
Production server and adapter dependencies do not include Node or Playwright.

From the repository root, with the project's Python environment activated:

```sh
python -m pip install -r requirements-core.txt
npm ci --prefix tests/browser --ignore-scripts --no-audit --no-fund
tests/browser/node_modules/.bin/playwright install chromium webkit
python tests/browser/run.py
node tests/browser/charts.cjs
```

Linux CI installs browser system libraries with `playwright install --with-deps`.
The workflow uses Node 24 and Python 3.14. Playwright is pinned in the package and
lockfile. The Python harness starts an ephemeral loopback server, waits for its
readiness endpoint, runs Chromium desktop and WebKit phone workflows, then stops
the process and deletes the fictional runtime even if a check fails.

The fixture contains 4,800 observations across four replicas, above the former
4,000-observation query budget. The dashboard suite checks exact source totals,
14-bin histograms, endpoint/event completeness and JSON export in memory, as well
as scope and fixed-range links, reload, browser history,
period navigation, event investigation, query cancellation and supersession,
freshness expiry without polling, offline/reconnect, invalid link recovery,
monitor diagnostic failure/recovery, readiness, 5 GiB quota presentation, RAM epoch
changes, rejected configuration diagnostics, and chart keyboard/touch behavior.
The standalone chart suite adds all four browser/device combinations and SVG
coordinate-transform cases. No screenshots, traces or downloaded observations
are written by default.

For optional **fictional** visual previews only:

```sh
BOBOWATCH_BROWSER_OUTPUT=/tmp/bobowatch-fictional-previews python tests/browser/run.py
```

Existing bundled Playwright installations can be used locally through Node's
`NODE_PATH`; CI always installs the locked development dependencies.
