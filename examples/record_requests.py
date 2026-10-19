"""Exercise a configured producer without installing the adapter as a package."""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bobowatch import Monitor  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replica", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8720/v1/ingest")
    args = parser.parse_args()
    monitor = Monitor(
        replica=args.replica,
        token_file=args.token_file,
        endpoint=args.endpoint,
        revision="manual-example",
        interval=2,
    )
    monitor.event("Explicit instrumentation example started")
    for index in range(10):
        monitor.record_request(0.025 * (index + 1), 503 if index == 9 else 200)
    time.sleep(0.01)
    if not monitor.flush():
        raise SystemExit("Telemetry was not accepted; check the runtime and credential scope")
    print("Recorded ten fictional requests and one event.")


if __name__ == "__main__":
    main()
