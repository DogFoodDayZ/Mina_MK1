import urllib.request
import urllib.error
from urllib.parse import urlparse
from typing import Any, Dict

def web_fetch_impl(url: str, timeout: int = 10) -> Dict[str, Any]:
    # Validate URL
    if not url or not isinstance(url, str):
        return {
            "ok": False,
            "result": None,
            "error": "no_url_provided"
        }

    # Basic sanity check
    if not (url.startswith("http://") or url.startswith("https://")):
        return {
            "ok": False,
            "result": None,
            "error": "invalid_url_scheme"
        }

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")

            # Only allow text-like content
            if "text" not in ctype and "json" not in ctype:
                return {
                    "ok": False,
                    "result": None,
                    "error": f"blocked_content_type: {ctype}"
                }

            max_bytes = 1_000_000
            content_length = r.headers.get("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) > max_bytes:
                        return {
                            "ok": False,
                            "result": None,
                            "error": "content_too_large"
                        }
                except ValueError:
                    pass

            raw_data = r.read(max_bytes + 1)
            if len(raw_data) > max_bytes:
                return {
                    "ok": False,
                    "result": None,
                    "error": "content_too_large"
                }

            data = raw_data.decode("utf-8", errors="ignore")

        return {
            "ok": True,
            "result": {"content": data},
            "error": None
        }

    except urllib.error.HTTPError as e:
        host = ""
        try:
            host = (urlparse(url).netloc or "").lower()
        except Exception:
            host = ""

        if e.code == 404 and "github.com" in host:
            return {
                "ok": False,
                "result": None,
                "error": "http_error_404: github_not_found_or_private_repo_or_invalid_path",
            }

        return {
            "ok": False,
            "result": None,
            "error": f"http_error_{e.code}"
        }

    except urllib.error.URLError as e:
        return {
            "ok": False,
            "result": None,
            "error": f"url_error: {str(e.reason)}"
        }

    except Exception as e:
        return {
            "ok": False,
            "result": None,
            "error": f"exception: {type(e).__name__}: {str(e)}"
        }

def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    url = args.get("url") or ""
    timeout = args.get("timeout", 10)

    # Convert timeout to int safely
    try:
        timeout = int(timeout)
    except Exception:
        return {
            "ok": False,
            "result": None,
            "error": "invalid_timeout_type"
        }

    # Enforce safe timeout range
    if timeout < 1 or timeout > 30:
        return {
            "ok": False,
            "result": None,
            "error": "timeout_out_of_range_1_to_30"
        }

    return web_fetch_impl(url, timeout)

# Optional schema to help the model
tool_entry.schema = {
    "description": "Fetch text or JSON content from a URL.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "HTTP/HTTPS URL to fetch."
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (1–30).",
                "minimum": 1,
                "maximum": 30
            }
        },
        "required": ["url"]
    }
}
