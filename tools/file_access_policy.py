import os
from typing import Iterable, List, Optional, Tuple


DEFAULT_FULL_ACCESS_ROOT = r"E:\workspace"
DEFAULT_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def normalize_tool_path(path: str) -> str:
    raw = str(path or "").strip().strip('"\'')
    if not raw:
        return ""
    expanded = os.path.expanduser(os.path.expandvars(raw))
    if not os.path.isabs(expanded):
        expanded = os.path.abspath(expanded)
    return os.path.abspath(expanded)


def full_access_root() -> str:
    cfg = str(os.getenv("MK1_FULL_ACCESS_ROOT", DEFAULT_FULL_ACCESS_ROOT) or DEFAULT_FULL_ACCESS_ROOT).strip()
    return normalize_tool_path(cfg)


def full_access_roots() -> List[str]:
    roots: List[str] = []

    def _add(path: str) -> None:
        normalized = normalize_tool_path(path)
        if normalized and normalized not in roots:
            roots.append(normalized)

    _add(os.getenv("MK1_FULL_ACCESS_ROOT", DEFAULT_FULL_ACCESS_ROOT))

    raw_extra = str(os.getenv("MK1_FULL_ACCESS_ROOTS", "") or "").strip()
    if raw_extra:
        for item in raw_extra.split(os.pathsep):
            _add(item)

    # Mina must always be able to write inside the active project tree even if
    # the legacy personal workspace root points elsewhere.
    _add(DEFAULT_PROJECT_ROOT)
    return roots


def _is_within(path: str, root: str) -> bool:
    p = normalize_tool_path(path)
    r = normalize_tool_path(root)
    if not p or not r:
        return False
    try:
        p_cmp = os.path.normcase(p)
        r_cmp = os.path.normcase(r)
        return os.path.commonpath([p_cmp, r_cmp]) == r_cmp
    except Exception:
        return False


def authorize_write_paths(
    paths: Iterable[str],
    authorized_write: bool,
    auth_token: str = "",
) -> Tuple[bool, Optional[str], List[str]]:
    normalized: List[str] = []
    outside: List[str] = []
    roots = full_access_roots()

    for path in paths:
        n = normalize_tool_path(path)
        if not n:
            continue
        normalized.append(n)
        if not any(_is_within(n, root) for root in roots):
            outside.append(n)

    if not outside:
        return True, None, normalized

    if not bool(authorized_write):
        return (
            False,
            (
                "write_not_authorized_outside_workspace: set authorized_write=true "
                "for writes outside E:\\workspace"
            ),
            normalized,
        )

    required_token = str(os.getenv("MK1_WRITE_AUTH_TOKEN", "") or "").strip()
    if required_token:
        provided = str(auth_token or "").strip()
        if provided != required_token:
            return False, "write_auth_token_invalid", normalized

    return True, None, normalized
