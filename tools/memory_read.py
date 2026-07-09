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

    def _norm_token(tok: str):
        t = (tok or "").strip().lower()
        if len(t) > 3 and t.endswith("s"):
            t = t[:-1]
        return t

    def content_tokens(text: str):
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        out = set()
        for t in tokens:
            n = _norm_token(t)
            if n and n not in stop_tokens and len(n) > 1:
                out.add(n)
        return out

    def rank_by_overlap(query_text: str, items, keep_if_no_overlap: bool = False):
        q_tokens = content_tokens(query_text)
        if not q_tokens:
            return list(items or []) if keep_if_no_overlap else []

        ranked = []
        for item in items or []:
            text = (item.get("text") or "")
            t_tokens = content_tokens(text)
            overlap = len(q_tokens & t_tokens)
            if overlap > 0:
                ranked.append((overlap, item))

        ranked.sort(key=lambda x: x[0], reverse=True)
        if ranked:
            return [item for _, item in ranked]

        if keep_if_no_overlap:
            # Keep semantic ranking order as provided by memory.search.
            return [item for item in items or [] if (item.get("text") or "").strip()]

        return []

    def split_query_parts(query_text: str):
        q = (query_text or "").strip()
        if not q:
            return []
        if " and " not in q.lower() and "," not in q:
            return []

        raw_parts = re.split(r"\band\b|,", q, flags=re.IGNORECASE)
        out = []
        for p in raw_parts:
            s = re.sub(r"\?", "", p).strip()
            s = re.sub(r"\b(what is|what's|what are|tell me|remember|my)\b", " ", s, flags=re.IGNORECASE)
            s = re.sub(r"\s+", " ", s).strip(" .;:")
            if s:
                out.append(s)
        return out[:4]

    def recall_once(mem, query_text: str, top_k: int):
        # Prefer explicit user-authored facts first.
        results = mem.search(
            query_text,
            top_k=top_k,
            include_kinds=["fact", "preference", "procedure"],
            include_tags=["user_memory"],
        )
        results = post_filter_results(query_text, rank_by_overlap(query_text, results, keep_if_no_overlap=True))[:top_k]

        # If semantic recall misses, do a lexical pass over explicit user facts.
        if not results:
            user_facts = mem.recent_memories(
                top_k=max(top_k * 20, 100),
                include_kinds=["fact", "preference", "procedure"],
                include_tags=["user_memory"],
            )

            results = post_filter_results(query_text, rank_by_overlap(query_text, user_facts))[:top_k]

        # Fall back to long-term promoted facts if explicit user facts are absent.
        if not results:
            results = mem.search(
                query_text,
                top_k=top_k,
                include_kinds=["fact", "preference", "procedure"],
                include_tags=["long_term"],
            )
            results = post_filter_results(query_text, rank_by_overlap(query_text, results, keep_if_no_overlap=True))[:top_k]

        # Fall back to broader recall.
        if not results:
            results = mem.search(
                query_text,
                top_k=top_k,
                include_kinds=["fact", "preference", "procedure", "interaction"],
            )
            results = post_filter_results(query_text, rank_by_overlap(query_text, results))[:top_k]

        if not results:
            results = mem.search(
                query_text,
                top_k=top_k,
            )
            results = post_filter_results(query_text, rank_by_overlap(query_text, results))[:top_k]

        return results

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
        parts = split_query_parts(query)
        merged = []

        for item in recall_once(mem, query, top_k=max(top_k, len(parts) or 1)):
            merged.append(item)

        for part in parts:
            for item in recall_once(mem, part, top_k=1):
                merged.append(item)

        # Dedupe while preserving order.
        results = []
        seen = set()
        for item in merged:
            txt = " ".join(str(item.get("text") or "").strip().lower().split())
            if not txt or txt in seen:
                continue
            seen.add(txt)
            results.append(item)
            if len(results) >= max(top_k, len(parts) or 1):
                break

        return {
            "ok": True,
            "results": results
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}
