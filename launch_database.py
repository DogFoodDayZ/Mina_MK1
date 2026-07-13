import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch MK1 embed server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8084)
    args = parser.parse_args()

    script = Path(__file__).parent / "Memory_server" / "mk1_embed_server.py"
    cmd = [
        sys.executable,
        str(script),
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
