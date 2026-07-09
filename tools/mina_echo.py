def tool_entry(args):
    return {"ok": True, "result": {"msg": "Hello from Mina"}, "error": None}

if __name__ == "__main__":
    import json
    print(json.dumps(tool_entry({})))
