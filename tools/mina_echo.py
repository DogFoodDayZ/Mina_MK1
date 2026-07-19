def tool_entry(args):
    return {"ok": True, "result": {"msg": "Hello from Mina"}, "error": None}


tool_entry.schema = {
    "description": "Return a simple Mina greeting for smoke checks.",
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}

if __name__ == "__main__":
    import json
    print(json.dumps(tool_entry({})))
