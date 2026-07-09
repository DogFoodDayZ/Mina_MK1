def tool_entry(args):
    from memory.mk1_memory import MK1Memory
    import re

    stop_tokens = {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "can",
        "did", "do", "does", "for", "from", "how", "i", "in", "is", "it",
        "me", "my", "of", "on", "or", "our", "so", "that", "the", "to",
        "was", "were", "what", "when", "where", "which", "who", "why", "you",
        "your",
    }

    def content_tokens(text: str):
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        return {t for t in tokens if t not in stop_tokens and len(t) > 1}

    def rank_by_overlap(query_text: str, items):
        q_tokens = content_tokens(query_text)
        if not q_tokens:
            return []

        ranked = []
        for item in items or []:
            text = (item.get("text") or "")
            t_tokens = content_tokens(text)
            overlap = len(q_tokens & t_tokens)
            if overlap > 0:
                ranked.append((overlap, item))

        ranked.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in ranked]

    def post_filter_results(query_text: str, items):
        q_norm = " ".join((query_text or "").strip().lower().split())
        out = []
        for item in items or []:
            text = (item.get("text") or "").strip()
            if not text:
                continue

            t_norm = " ".join(text.lower().split())

            # Never return the user query itself as "memory recall".
            if q_norm and t_norm == q_norm:
                continue

            # Avoid question-like echoes in fallback recall results.
            if text.endswith("?"):
                continue

            if t_norm.startswith((
                "what ", "why ", "how ", "when ", "where ", "who ", "which ",
                "is ", "are ", "am ", "can ", "could ", "do ", "did ",
                "does ", "will ", "would ", "should ",
            )):
                continue

            out.append(item)

        return out

    query = args.get("query", "")
    top_k = int(args.get("top_k", 1))

    if not query:
        return {"ok": False, "error": "No query provided"}

    try:
        mem = MK1Memory()

        # Prefer explicit user-authored facts first.
        results = mem.search(
            query,
            top_k=top_k,
            include_kinds=["fact", "preference", "procedure"],
            include_tags=["user_memory"],
        )
        results = post_filter_results(query, rank_by_overlap(query, results))[:top_k]

        # If semantic recall misses, do a lexical pass over explicit user facts.
        if not results:
            user_facts = mem.recent_memories(
                top_k=max(top_k * 20, 100),
                include_kinds=["fact", "preference", "procedure"],
                include_tags=["user_memory"],
            )

            results = post_filter_results(query, rank_by_overlap(query, user_facts))[:top_k]

        # Fall back to long-term promoted facts if explicit user facts are absent.
        if not results:
            results = mem.search(
                query,
                top_k=top_k,
                include_kinds=["fact", "preference", "procedure"],
                include_tags=["long_term"],
            )
            results = post_filter_results(query, rank_by_overlap(query, results))[:top_k]

        # Fall back to broader recall.
        if not results:
            results = mem.search(
                query,
                top_k=top_k,
                include_kinds=["fact", "preference", "procedure", "interaction"],
            )
            results = post_filter_results(query, rank_by_overlap(query, results))[:top_k]

        if not results:
            results = mem.search(
                query,
                top_k=top_k,
            )
            results = post_filter_results(query, rank_by_overlap(query, results))[:top_k]

        return {
            "ok": True,
            "results": results
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}
