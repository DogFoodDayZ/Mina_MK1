import os
import shutil
from typing import Any, Dict

def safe_copy_impl(src: str, dst: str, verbose: bool = False, chunk_size: int = 1024 * 1024) -> Dict[str, Any]:
    try:
        # Validate src
        if not src:
            return {
                "ok": False,
                "result": None,
                "error": "no_src_provided",
            }

        if not dst:
            return {
                "ok": False,
                "result": None,
                "error": "no_dst_provided",
            }

        # Expand ~ and env vars
        src = os.path.expanduser(os.path.expandvars(src))
        dst = os.path.expanduser(os.path.expandvars(dst))

        # Normalize to absolute paths
        if not os.path.isabs(src):
            src = os.path.abspath(src)
        if not os.path.isabs(dst):
            dst = os.path.abspath(dst)

        # Ensure src exists and is a file
        if not os.path.exists(src):
            return {
                "ok": False,
                "result": None,
                "error": f"src_not_found: {src}",
            }

        if not os.path.isfile(src):
            return {
                "ok": False,
                "result": None,
                "error": f"src_not_file: {src}",
            }

        # If dst is a directory, copy into it using the same file name
        if os.path.isdir(dst) or dst.endswith(os.sep):
            dst = os.path.join(dst, os.path.basename(src))

        if not os.path.isabs(dst):
            dst = os.path.abspath(dst)

        # Prevent copying a file onto itself
        try:
            if os.path.exists(dst) and os.path.samefile(src, dst):
                return {
                    "ok": False,
                    "result": None,
                    "error": "src_and_dst_same_file",
                }
        except OSError:
            pass

        # Ensure destination directory exists
        dst_dir = os.path.dirname(dst)
        if dst_dir and not os.path.isdir(dst_dir):
            os.makedirs(dst_dir, exist_ok=True)

        total_size = os.path.getsize(src)
        copied = 0
        progress = []

        if verbose:
            progress.append(f"safe_copy: copying {total_size} bytes from {src} to {dst}")

        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            while True:
                chunk = fsrc.read(chunk_size)
                if not chunk:
                    break
                fdst.write(chunk)
                copied += len(chunk)
                if verbose and total_size > 0:
                    percent = int(copied / total_size * 100)
                    progress.append(f"safe_copy: {percent}% ({copied}/{total_size})")

        shutil.copystat(src, dst)

        if verbose:
            progress.append(f"safe_copy: completed {copied} bytes to {dst}")

        return {
            "ok": True,
            "result": {
                "copied": True,
                "src": src,
                "dst": dst,
                "bytes_copied": copied,
                "total_size": total_size,
                "progress": progress,
            },
            "error": None,
        }

    except Exception as e:
        return {
            "ok": False,
            "result": None,
            "error": str(e),
        }

def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    src = args.get("src") or ""
    dst = args.get("dst") or ""
    verbose = bool(args.get("verbose", False))
    chunk_size = int(args.get("chunk_size", 1024 * 1024))
    return safe_copy_impl(src, dst, verbose=verbose, chunk_size=chunk_size)

# Optional schema to help the model
tool_entry.schema = {
    "description": "Safely copy a file from src to dst, creating destination directories if needed.",
    "parameters": {
        "type": "object",
        "properties": {
            "src": {
                "type": "string",
                "description": "Source file path to copy from."
            },
            "dst": {
                "type": "string",
                "description": "Destination file path to copy to."
            },
            "verbose": {
                "type": "boolean",
                "description": "If true, print progress updates while copying."
            },
            "chunk_size": {
                "type": "integer",
                "description": "Number of bytes to copy per chunk when showing progress.",
                "default": 1048576
            }
        },
        "required": ["src", "dst"]
    }
}
