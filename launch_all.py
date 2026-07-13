import argparse
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch embed server and API together")
    parser.add_argument("--api-host", default="127.0.0.1")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--db-host", default="127.0.0.1")
    parser.add_argument("--db-port", type=int, default=8084)
    parser.add_argument("--db-wait-seconds", type=float, default=2.0)
    args = parser.parse_args()

    root = Path(__file__).parent
    launch_db = root / "launch_database.py"
    launch_api = root / "launch_api.py"

    db_proc = subprocess.Popen(
        [
            sys.executable,
            str(launch_db),
            "--host",
            args.db_host,
            "--port",
            str(args.db_port),
        ]
    )

    try:
        time.sleep(max(0.0, args.db_wait_seconds))
        api_code = subprocess.call(
            [
                sys.executable,
                str(launch_api),
                "--host",
                args.api_host,
                "--port",
                str(args.api_port),
            ]
        )
        return api_code
    finally:
        if db_proc.poll() is None:
            db_proc.terminate()
            try:
                db_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                db_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
