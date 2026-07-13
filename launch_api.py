import argparse
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch MK1 API server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "agent.server.mk1_api:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--no-access-log",
    ]
    if args.reload:
        cmd.append("--reload")

    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
