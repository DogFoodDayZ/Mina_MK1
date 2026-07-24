import re
from typing import Any, Dict, Optional


class PendingActionStore:
    def __init__(self) -> None:
        self._items: Dict[str, Dict[str, Any]] = {}

    def set(self, action_key: str, payload: Dict[str, Any]) -> None:
        key = str(action_key or "").strip().lower()
        if not key:
            return
        self._items[key] = dict(payload or {})

    def get(self, action_key: str) -> Optional[Dict[str, Any]]:
        key = str(action_key or "").strip().lower()
        if not key:
            return None
        payload = self._items.get(key)
        return dict(payload) if isinstance(payload, dict) else None

    def clear(self, action_key: str) -> None:
        key = str(action_key or "").strip().lower()
        if not key:
            return
        self._items.pop(key, None)


def is_directory_scan_confirmation(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    if low in {"yes scan now", "scan now", "yes scan", "scan it", "yes scan it"}:
        return True
    return re.search(r"\b(?:yes\s+scan\s+now|scan\s+now|start\s+scan|run\s+scan|begin\s+scan)\b", low) is not None


def is_directory_scan_cancel(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    if low in {"cancel scan", "stop scan", "no scan", "never mind scan"}:
        return True
    return re.search(r"\b(?:cancel\s+scan|stop\s+scan|abort\s+scan|don't\s+scan|do\s+not\s+scan)\b", low) is not None


def is_write_authorization_confirmation(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    if low in {"yes", "y", "yep", "yeah", "ok", "okay", "do it", "proceed", "continue", "allow", "authorize", "authorise"}:
        return True
    return re.search(r"\b(authorize|authorise|approve|approved|allow|proceed|continue|go\s+ahead)\b", low) is not None


def is_write_authorization_cancel(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    if low in {"no", "n", "cancel", "deny", "stop", "never mind", "nevermind"}:
        return True
    return re.search(r"\b(cancel|deny|stop|don't|do\s+not|abort)\b", low) is not None
