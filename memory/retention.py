from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Set


DURABLE_TAGS = {
    "authoritative_identity",
    "authoritative_profile",
    "durable",
    "long_term",
    "routing_correction",
    "verified_tool_result",
}

FAILED_DECISION_PATTERNS = [
    r"^\(lm studio error:",
    r"^\(error contacting",
    r"^i cannot .{1,120} from this setup",
    r"^(?:request|tool|action) (?:failed|error)",
]


def is_failed_decision_text(text: str) -> bool:
    import re

    return any(re.search(pattern, str(text or ""), re.IGNORECASE) is not None for pattern in FAILED_DECISION_PATTERNS)


def retention_day(timestamp: float) -> str:
    return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d")


def plan_conversation_retention(
    rows: Iterable[Dict[str, Any]],
    now_ts: float,
    interaction_retention_days: int,
    failed_decision_retention_days: int,
) -> Dict[str, Any]:
    interaction_cutoff = float(now_ts) - max(1, int(interaction_retention_days)) * 86400.0
    failed_cutoff = float(now_ts) - max(1, int(failed_decision_retention_days)) * 86400.0
    regular_by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    failed_ids: List[int] = []
    preserved_ids: List[int] = []
    recent_ids: List[int] = []

    for raw in rows or []:
        row = dict(raw or {})
        memory_id = int(row.get("id") or 0)
        timestamp = float(row.get("timestamp") or 0.0)
        tags: Set[str] = {
            str(tag or "").strip().lower()
            for tag in (row.get("tags") or [])
            if str(tag or "").strip()
        }

        if memory_id <= 0:
            continue
        if tags.intersection(DURABLE_TAGS):
            preserved_ids.append(memory_id)
            continue
        if "failed_decision" in tags or is_failed_decision_text(str(row.get("text") or "")):
            if timestamp < failed_cutoff:
                failed_ids.append(memory_id)
            else:
                recent_ids.append(memory_id)
            continue
        if timestamp < interaction_cutoff:
            regular_by_day[retention_day(timestamp)].append(row)
        else:
            recent_ids.append(memory_id)

    regular_ids = [
        int(row.get("id") or 0)
        for day in sorted(regular_by_day)
        for row in regular_by_day[day]
        if int(row.get("id") or 0) > 0
    ]
    return {
        "interaction_cutoff": interaction_cutoff,
        "failed_decision_cutoff": failed_cutoff,
        "regular_by_day": dict(regular_by_day),
        "regular_ids": regular_ids,
        "failed_ids": failed_ids,
        "delete_ids": failed_ids + regular_ids,
        "preserved_ids": preserved_ids,
        "recent_ids": recent_ids,
    }