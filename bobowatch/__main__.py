"""Standalone CLI. Importing bobowatch never imports the server dependency."""

import argparse
import fcntl
import logging
import signal
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from .config import initialize, load
from .protocol import Invalid

DEFAULT_STATE = Path.home() / ".local/state/bobowatch"


@contextmanager
def runtime_lock(config):
    path = (
        config.path.parent / "server.lock"
        if config.ram_required
        else config.database.with_suffix(".lock")
    )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as lock:
        path.chmod(0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Invalid("Another BoboWatch command owns this runtime") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def serve(config, *, simulate=False):
    try:
        from waitress import create_server
    except ImportError:
        raise Invalid(
            "Install core dependencies: python -m pip install -r requirements-core.txt"
        ) from None
    from .application import Application
    from .demo import Simulation
    from .storage import SQLiteStore

    with runtime_lock(config):
        if config.ram_required:
            from .provision import require_private
            from .ramdisk import validate_database

            validate_database(config, full=True)
            spill = config.database.parent / "tmp"
            spill.mkdir(mode=0o700, exist_ok=True)
            require_private(spill, directory=True)
            # Waitress output spooling also stays on the bounded RAM volume.
            tempfile.tempdir = str(spill)
        store = SQLiteStore(config)
        simulation, server = None, None
        previous_handlers = {}
        reload_stop = threading.Event()
        reload_thread = None
        try:
            application = Application(config, store)
            server = create_server(
                application,
                host=config.host,
                port=config.port,
                threads=4,
                connection_limit=32,
                channel_timeout=10,
                cleanup_interval=2,
                max_request_header_size=8192,
                max_request_body_size=262144,
                expose_tracebacks=False,
                clear_untrusted_proxy_headers=True,
                inbuf_overflow=262145,
                ident="BoboWatch",
            )

            def reload_loop():
                failed = False
                while not reload_stop.wait(2):
                    try:
                        application.reload()
                        failed = False
                    except Exception as error:
                        if not failed:
                            logging.getLogger(__name__).warning(
                                "Config reload rejected (%s); keeping prior settings",
                                type(error).__name__,
                            )
                        failed = True

            reload_thread = threading.Thread(
                target=reload_loop, name="bobowatch-config", daemon=True
            )
            reload_thread.start()
            if simulate:
                simulation = Simulation(config, store)
                simulation.start()

            def shutdown(signum, frame):
                raise KeyboardInterrupt

            for sig in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[sig] = signal.signal(sig, shutdown)
            print(f"BoboWatch: http://127.0.0.1:{config.port}", flush=True)
            if config.host == "0.0.0.0":
                print(
                    f"LAN access: all IPv4 interfaces on port {config.port}; use this computer's LAN IP",
                    flush=True,
                )
                for hostname in config.allowed_hosts:
                    print(f"LAN hostname: http://{hostname}:{config.port}", flush=True)
            print(f"Admin token file: {config.admin_token_file}", flush=True)
            print(f"SQLite history: {config.database}", flush=True)
            if simulate:
                print(
                    "Demo: two fictional sites; stop with Ctrl-C. No real applications are contacted.",
                    flush=True,
                )
            try:
                server.run()
            except KeyboardInterrupt:
                pass
        finally:
            reload_stop.set()
            if reload_thread:
                reload_thread.join(6)
            if simulation:
                simulation.close()
            if server:
                server.close()
                server.task_dispatcher.shutdown()
            store.close()
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(prog="python -m bobowatch")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "demo"):
        item = commands.add_parser(name)
        item.add_argument(
            "--state-dir",
            type=Path,
            default=DEFAULT_STATE
            if name == "init"
            else Path.home() / ".local/state/bobowatch-demo",
        )
        item.add_argument("--port", type=int, default=8720)
    for name in ("serve", "simulate", "backup"):
        item = commands.add_parser(name)
        item.add_argument("--config", type=Path, required=True)
        if name == "backup":
            item.add_argument("--output", type=Path, required=True)
    provision = commands.add_parser("provision")
    provision.add_argument("--runtime-dir", type=Path)
    for field in ("project", "site", "host", "replica"):
        provision.add_argument("--" + field, required=True)
    provision.add_argument("--token-file", type=Path, required=True)
    provision.add_argument("--environment", default="production")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "provision":
            import json

            from .provision import ensure_registration

            result = ensure_registration(
                runtime=args.runtime_dir,
                project=args.project,
                site=args.site,
                host=args.host,
                replica=args.replica,
                token_file=args.token_file,
                environment=args.environment,
            )
            print(json.dumps(result))
        elif args.command in ("init", "demo"):
            if not 1 <= args.port <= 65535:
                raise Invalid("Port must be between 1 and 65535")
            config = initialize(args.state_dir, demo=args.command == "demo", port=args.port)
            if args.command == "init":
                from .storage import SQLiteStore

                with runtime_lock(config):
                    store = SQLiteStore(config)
                    store.close()
                print(f"Initialized {config.path}")
            else:
                serve(config, simulate=True)
        else:
            config = load(args.config)
            if args.command == "serve":
                serve(config)
            elif args.command == "simulate":
                from .demo import Simulation

                simulation = Simulation(config)
                simulation.start()
                try:
                    while simulation.thread.is_alive():
                        simulation.thread.join(0.5)
                except KeyboardInterrupt:
                    pass
                finally:
                    simulation.close()
            else:
                from .storage import backup_sqlite

                backup_sqlite(config.database, args.output)
                print(f"Backup saved to {args.output.expanduser().resolve()}")
    except (Invalid, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"BoboWatch: {exc}\n")


if __name__ == "__main__":
    main()
