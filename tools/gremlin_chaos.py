import random
import time
from typing import Any, Dict

def gremlin_chaos_impl(level: int) -> Dict[str, Any]:
    """
    Controlled chaos generator.
    Level 1-5:
      1 = harmless randomness
      5 = aggressive stress test (still safe)
    """
    # Validate range
    if not isinstance(level, int):
        return {"ok": False, "result": None, "error": "level_must_be_int"}

    if level < 1 or level > 5:
        return {"ok": False, "result": None, "error": "level_out_of_range"}

    actions = [
        "shuffling internal state",
        "randomizing delay",
        "injecting entropy",
        "simulating load spike",
        "triggering pseudo-failure"
    ]

    chosen = random.sample(actions, level)

    # Simulated chaos delay
    time.sleep(0.1 * level)

    return {
        "ok": True,
        "result": {
            "status": "chaos_complete",
            "actions": chosen,
            "level": level
        },
        "error": None
    }

def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    # Default level = 1 if not provided
    level = args.get("level", 1)

    # Convert strings like "3" to int
    try:
        level = int(level)
    except Exception:
        return {"ok": False, "result": None, "error": "invalid_level_type"}

    return gremlin_chaos_impl(level)

# Optional schema (recommended for better model behavior)
tool_entry.schema = {
    "description": "Generate controlled gremlin chaos at levels 1-5.",
    "parameters": {
        "type": "object",
        "properties": {
            "level": {
                "type": "integer",
                "description": "Chaos intensity from 1 (mild) to 5 (maximum).",
                "minimum": 1,
                "maximum": 5
            }
        },
        "required": []
    }
}
