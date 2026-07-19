def tool_entry(args):
    from memory.mk1_memory import MK1Memory

    text = args.get("text", "")
    kind = args.get("kind", "fact")
    tags = args.get("tags", [])

    if not text:
        return {"ok": False, "error": "No text provided"}

    # --- CLEANING LAYER (fixes your problem) ---
    def clean_memory_text(raw: str) -> str:
        t = raw.strip()
        low = t.lower()

        prefixes = [
            "remember ", "remember that ",
            "recall ", "recall that ",
            "store ", "store that ",
            "save ", "save that ",
            "note ", "note that ",
            "keep ", "keep that "
        ]

        for p in prefixes:
            if low.startswith(p):
                t = t[len(p):].strip()
                low = t.lower()
                break

        # Remove trailing command-like phrases so the stored memory is clean
        suffixes = [
            "please store that",
            "please remember that",
            "please save that",
            "please keep that",
            "store that",
            "remember that",
            "save that",
            "keep that",
            "please store this",
            "please remember this",
            "please save this",
            "please keep this",
            "store this",
            "save this",
            "keep this",
        ]

        for suffix in suffixes:
            if low.endswith(suffix):
                t = t[: -len(suffix)].strip()
                low = t.lower()
                break

        if low.startswith("that "):
            t = t[5:].strip()

        return t

    cleaned = clean_memory_text(text)
    # -------------------------------------------

    try:
        mem = MK1Memory()

        dedupe_id = None
        if kind in ["fact", "preference", "procedure"] and "user_memory" in tags:
            dedupe_id = mem.find_memory_id_by_text(
                cleaned,
                include_kinds=[kind],
                include_tags=["user_memory"],
            )

        if dedupe_id is not None:
            return {
                "ok": True,
                "id": dedupe_id,
                "stored": cleaned,
                "deduplicated": True,
            }

        mem_id = mem.add_memory(cleaned, kind=kind, tags=tags)

        if mem_id is None:
            return {
                "ok": False,
                "error": "memory_write_failed: add_memory returned None",
            }

        return {
            "ok": True,
            "id": mem_id,
            "stored": cleaned
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}


tool_entry.schema = {
    "description": "Write a memory fact, preference, or procedure into Mina's memory store.",
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Memory text to store.",
            },
            "kind": {
                "type": "string",
                "description": "Memory kind such as fact, preference, procedure, or note.",
                "default": "fact",
            },
            "tags": {
                "type": "array",
                "items": {
                    "type": "string",
                },
                "description": "Optional memory tags.",
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    },
}

