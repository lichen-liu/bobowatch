# First application integration

Local provisioning, ASGI HTTP instrumentation and live inventory/endpoint reload
are implemented. The [installed runtime](runtime.md) adds verified RAM storage,
retention and on-demand supervision. Install it before enabling Flightrepo.

## Paths and ownership

Installed macOS runtime resolution is explicit `--runtime-dir`, then
`BOBOWATCH_RUNTIME_DIR`, then `~/Library/Application Support/fyi.boboji.watch`.
The legacy `init`/`demo` commands still use their existing `.local/state` defaults;
no old runtime or history is migrated implicitly. Provisioning requires an already
initialized non-demo `monitor.toml`, owned by the invoking user and private.

## Offline registration

```sh
python -m bobowatch provision \
  --runtime-dir /absolute/private/watch-runtime \
  --project flightrepo --site flyji-production --host mac-01 \
  --replica flight-web-r01 \
  --token-file /absolute/private/flight-runtime/tokens/bobowatch.token
```

The destination directory must already be private and owned by you. The command
locks registration, validates the full candidate, reuses existing credentials on
repeat deployments, atomically publishes `registrations.json` and writes only the
producer secret to the requested private file. Output is JSON containing the
endpoint, replica, interval and token-file path; never the secret itself. A changed
host/site for the same replica or a changed project/environment for the same site
is refused. Register new IDs instead. Real producers cannot register into a demo.
The command performs no network IO, SQL writes or process startup. It runs even
when BoboWatch is stopped. Different operating-system users and remote registration
are outside this first provisioning interface.

The server reloads TOML, registration inventory and referenced token contents every
two seconds. It validates first, commits inventory through its one SQL writer and
then publishes the matching authentication snapshot. Invalid changes keep the
prior effective configuration; listener/database/demo changes require restart.
In-flight requests may finish under their prior authentication snapshot. Future
requests use the new token after publication. Removed targets must remain marked
`retired=true`, preserving the existing inventory/history contract.

## HTTP instrumentation

`bobowatch.asgi.InstrumentedApp(app, factory)` wraps the actual outer served app.
The factory constructs a Monitor per serving process during lifespan startup (or
first HTTP request in a server without lifespan), avoiding import-time threads.
Shutdown joins the sender off the event loop. Factories that fail leave the app
available and report only an error class.

The wrapper observes forwarded ASGI messages without buffering or draining bodies.
It records once at the final response body, clears active work on exceptions and
records only registered route templates resolved by the framework; raw paths and
queries do not become dimensions. Streaming and background work after the response
must not produce duplicate completions. Request body bytes remain unknown when
the application did not fully consume the body. Body-byte counts describe bytes
passing this wrapper, not complete TCP/TLS traffic. Server-level rejections before
ASGI dispatch (such as Uvicorn connection limits) are outside this measurement.

## Application connection settings

A consuming app's private JSON settings can contain:

```json
{
  "monitoring": {
    "enabled": true,
    "endpoint": "http://127.0.0.1:8720/v1/ingest",
    "replica": "flight-web-r01",
    "token_file": "tokens/bobowatch.token",
    "interval": 15
  }
}
```

`ConfiguredMonitor(settings_path, routes=..., revision=...)` resolves relative
credential paths against that settings file. Its existing sender thread rereads
connection settings before delivery. Endpoint/token/interval changes preserve the
run UUID and cumulative counters; invalid settings keep the previous destination.
Changing replica identity requires an app restart. Disabling an already running
sender suspends delivery; enabling an app initially started with monitoring off
requires a restart. Token contents are read on each delivery. HTTP is permitted
only on loopback; other destinations require HTTPS.

The public repository can be a pinned Git submodule. Consumers package only the
standard-library adapter modules into their installed release; Waitress, the
monitoring SQL database and dashboard are not dependencies of the serving app.

## Runtime rollout

See the [complete operator walkthrough](runtime.md#flightrepo-rollout). Deployment
registers credentials without starting BoboWatch. RAM storage and local admin are
implemented; ingestion progress and host metrics remain future work. Flightrepo's
independent ingestion RAM disk and worker are not managed by BoboWatch.
