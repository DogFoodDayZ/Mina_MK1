import json
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

GITHUB_API_BASE = "https://api.github.com"
MAX_RESPONSE_BYTES = 2_000_000


def _fetch_url(url: str, token: Optional[str] = None) -> Dict[str, Any]:
    headers = {
        "User-Agent": "Mina-GitHub-Tool/1.0",
        "Accept": "application/vnd.github.v3+json",
    }
    if token:
        headers["Authorization"] = f"token {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return {
                    "ok": False,
                    "error": "response_too_large",
                    "result": None,
                }
            text = raw.decode("utf-8", errors="ignore")
            data = json.loads(text)
            return {"ok": True, "result": data, "error": None}

    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="ignore")
            error_data = json.loads(body)
            message = error_data.get("message", str(e))
        except Exception:
            message = str(e)
        return {
            "ok": False,
            "error": f"http_error_{e.code}: {message}",
            "result": None,
        }

    except urllib.error.URLError as e:
        return {
            "ok": False,
            "error": f"url_error: {e.reason}",
            "result": None,
        }

    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "invalid_json_response",
            "result": None,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": f"exception: {type(e).__name__}: {e}",
            "result": None,
        }


def _build_repo_url(owner: str, repo: str, path: str = "") -> str:
    repo = repo.strip()
    owner = owner.strip()
    path = path.strip().lstrip("/")
    return f"{GITHUB_API_BASE}/repos/{owner}/{repo}/{path}" if path else f"{GITHUB_API_BASE}/repos/{owner}/{repo}"


def _build_user_url(owner: str, path: str = "repos") -> str:
    owner = owner.strip()
    return f"{GITHUB_API_BASE}/users/{owner}/{path}"


def _validate_owner_repo(owner: str, repo: str) -> Optional[str]:
    if not owner or not isinstance(owner, str):
        return "owner is required"
    if not repo or not isinstance(repo, str):
        return "repo is required"
    return None


def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    action = (args.get("action") or "repo_info").strip().lower()
    owner = args.get("owner") or ""
    repo = args.get("repo") or ""
    token = args.get("token")
    branch = args.get("branch") or "main"
    per_page = int(args.get("per_page", 20) or 20)
    issue_state = (args.get("issue_state") or "open").strip().lower()

    if action == "list_repos":
        if not owner:
            return {"ok": False, "result": None, "error": "owner is required for list_repos"}
        url = _build_user_url(owner, "repos") + f"?per_page={per_page}"
        return _fetch_url(url, token)

    if action in {"repo_info", "latest_commit", "open_issues", "list_releases", "list_branches"}:
        err = _validate_owner_repo(owner, repo)
        if err:
            return {"ok": False, "result": None, "error": err}

        if action == "repo_info":
            url = _build_repo_url(owner, repo)
            return _fetch_url(url, token)

        if action == "latest_commit":
            url = _build_repo_url(owner, repo, f"commits/{branch}")
            return _fetch_url(url, token)

        if action == "open_issues":
            url = _build_repo_url(owner, repo, "issues") + f"?state={issue_state}&per_page={per_page}"
            return _fetch_url(url, token)

        if action == "list_releases":
            url = _build_repo_url(owner, repo, "releases") + f"?per_page={per_page}"
            return _fetch_url(url, token)

        if action == "list_branches":
            url = _build_repo_url(owner, repo, "branches") + f"?per_page={per_page}"
            return _fetch_url(url, token)

    return {
        "ok": False,
        "result": None,
        "error": f"unknown_action: {action}",
    }


tool_entry.schema = {
    "description": "Query GitHub repository metadata, commits, issues, branches, and releases.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "GitHub action to perform.",
                "enum": ["repo_info", "latest_commit", "open_issues", "list_releases", "list_branches", "list_repos"],
            },
            "owner": {
                "type": "string",
                "description": "GitHub repository owner or user.",
            },
            "repo": {
                "type": "string",
                "description": "GitHub repository name.",
            },
            "branch": {
                "type": "string",
                "description": "Branch to inspect for latest_commit.",
            },
            "per_page": {
                "type": "integer",
                "description": "Number of items to return per request.",
                "minimum": 1,
                "maximum": 100,
            },
            "issue_state": {
                "type": "string",
                "description": "Issue state filter for open_issues.",
                "enum": ["open", "closed", "all"],
            },
            "token": {
                "type": "string",
                "description": "Optional GitHub API token for private or authenticated requests.",
            },
        },
        "required": ["action"],
    },
}
