import html
import json
import re
import urllib.parse
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


MAX_SEARCH_BYTES = 1_000_000
MAX_PAGE_BYTES = 300_000
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


SEARCH_MODES = {"balanced", "strict", "wide"}

# Broad low-signal domains that commonly dominate generic queries.
LOW_SIGNAL_DOMAIN_PATTERNS = [
    r"(^|\.)bestbuy\.com$",
    r"(^|\.)merriam-webster\.com$",
    r"(^|\.)dictionary\.cambridge\.org$",
    r"(^|\.)usdictionary\.com$",
]

TOPIC_PREFERRED_DOMAINS = {
    "travel": [
        "google.com/travel",
        "skyscanner.com",
        "expedia.com",
        "kayak.com",
        "flights.google.com",
        "tripadvisor.com",
        "booking.com",
        "airlines",
    ],
    "coding": [
        "docs.",
        "developer.",
        "github.com",
        "stackoverflow.com",
        "pypi.org",
        "npmjs.com",
        "readthedocs",
        "mozilla.org",
    ],
    "news": [
        "reuters.com",
        "apnews.com",
        "bbc.com",
        "nytimes.com",
        "wsj.com",
        "theguardian.com",
    ],
    "health": [
        "cdc.gov",
        "nih.gov",
        "who.int",
        "mayoclinic.org",
        "nhs.uk",
        "webmd.com",
    ],
}


def _extract_host(url: str) -> str:
    try:
        host = (urllib.parse.urlparse(url).netloc or "").lower().strip()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def _query_tokens(query: str) -> List[str]:
    words = re.findall(r"[a-z0-9]{3,}", (query or "").lower())
    stop = {
        "the", "and", "for", "from", "with", "that", "this", "into", "onto", "about",
        "best", "top", "find", "search", "show", "list", "what", "where", "when",
    }
    return [w for w in words if w not in stop]


def _detect_topic(query: str) -> str:
    q = (query or "").lower()
    if re.search(r"\b(flight|airline|airport|layover|itinerary|fare|hotel|travel|trip|route|visa)\b", q):
        return "travel"
    if re.search(r"\b(python|javascript|typescript|java|c\+\+|rust|api|sdk|library|framework|bug|error|stack trace|docs?)\b", q):
        return "coding"
    if re.search(r"\b(breaking|headline|news|update|election|war|economy)\b", q):
        return "news"
    if re.search(r"\b(symptom|disease|medicine|dose|treatment|clinic|health|diagnosis|vaccine)\b", q):
        return "health"
    return "general"


def _is_low_signal(url: str, title: str) -> bool:
    host = _extract_host(url)
    url_low = (url or "").lower()

    # Filter common ad/click-tracker redirect endpoints.
    if "duckduckgo.com/y.js" in url_low:
        return True
    if "bing.com/aclick" in url_low:
        return True
    if "ad_domain=" in url_low:
        return True

    for p in LOW_SIGNAL_DOMAIN_PATTERNS:
        if re.search(p, host, flags=re.IGNORECASE):
            return True

    t = (title or "").lower()
    if re.search(r"\bdefinition\b|\bmeaning\b|\bdictionary\b", t):
        return True
    return False


def _score_result(result: Dict[str, str], query: str, topic: str) -> int:
    title = str(result.get("title") or "")
    snippet = str(result.get("snippet") or "")
    url = str(result.get("url") or "")
    host = _extract_host(url)
    text = (title + " " + snippet).lower()
    tokens = _query_tokens(query)

    score = 0
    for token in tokens:
        if token in text:
            score += 3
        if token in host:
            score += 2

    prefs = TOPIC_PREFERRED_DOMAINS.get(topic, [])
    for marker in prefs:
        if marker in (host + url.lower()):
            score += 8
            break

    if _is_low_signal(url, title):
        score -= 10

    return score


def _rerank_results(results: List[Dict[str, str]], query: str, topic: str, mode: str, max_results: int) -> List[Dict[str, str]]:
    if not results:
        return []

    deduped: List[Dict[str, str]] = []
    seen = set()
    for row in results:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        if not title or not url:
            continue
        key = (title.lower(), url.lower())
        if key in seen:
            continue
        seen.add(key)
        deduped.append({
            "title": title,
            "url": url,
            "snippet": str(row.get("snippet") or "").strip(),
        })

    if mode == "wide":
        return deduped[:max_results]

    scored = [
        (idx, _score_result(row, query=query, topic=topic), row)
        for idx, row in enumerate(deduped)
    ]
    scored.sort(key=lambda x: (-x[1], x[0]))

    if mode == "strict":
        filtered = [row for _, score, row in scored if score >= 2 and not _is_low_signal(row.get("url", ""), row.get("title", ""))]
        return filtered[:max_results]

    # balanced: keep strong results first; if too few survive, include weaker scored rows.
    preferred = [row for _, score, row in scored if score >= 0 and not _is_low_signal(row.get("url", ""), row.get("title", ""))]
    if len(preferred) >= max_results:
        return preferred[:max_results]

    out = list(preferred)
    # Prefer remaining non-low-signal rows before allowing low-signal backfill.
    for _, _, row in scored:
        if row in out:
            continue
        if _is_low_signal(row.get("url", ""), row.get("title", "")):
            continue
        out.append(row)
        if len(out) >= max_results:
            return out[:max_results]

    # In balanced mode, prefer fewer clean results over padded low-signal links.
    if out:
        return out[:max_results]

    # If everything scored as low-signal, still return something rather than empty.
    return [row for _, _, row in scored][:max_results]


def _decode_duckduckgo_redirect(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            q = urllib.parse.parse_qs(parsed.query)
            uddg = q.get("uddg", [""])[0]
            if uddg:
                return urllib.parse.unquote(uddg)
    except Exception:
        pass
    return url


def _strip_tags(text: str) -> str:
    cleaned = re.sub(r"<script[\\s\\S]*?</script>", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"<style[\\s\\S]*?</style>", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\\s+", " ", cleaned).strip()
    return cleaned


def _fetch_text_url(url: str, timeout: int, max_bytes: int) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "DNT": "1",
            "Connection": "keep-alive",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type", "") or "").lower()
            if "text" not in ctype and "html" not in ctype and "json" not in ctype:
                return {"ok": False, "error": f"blocked_content_type: {ctype}", "text": ""}

            raw = r.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return {"ok": False, "error": "content_too_large", "text": ""}

            text = raw.decode("utf-8", errors="ignore")
            return {"ok": True, "error": None, "text": text}

    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"http_error_{e.code}", "text": ""}
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"url_error: {e.reason}", "text": ""}
    except Exception as e:
        return {"ok": False, "error": f"exception: {type(e).__name__}: {e}", "text": ""}


def _fetch_text_url_requests(url: str, timeout: int, max_bytes: int) -> Dict[str, Any]:
    """Fetch URL using requests library with session handling for better bot detection bypass."""
    if not HAS_REQUESTS:
        return {"ok": False, "error": "requests_not_available", "text": ""}
    
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "DNT": "1",
            "Connection": "keep-alive",
            "Referer": "https://duckduckgo.com/",
        })
        
        response = session.get(url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
        
        ctype = (response.headers.get("Content-Type", "") or "").lower()
        if "text" not in ctype and "html" not in ctype and "json" not in ctype:
            return {"ok": False, "error": f"blocked_content_type: {ctype}", "text": ""}
        
        text = response.text
        if len(text.encode('utf-8')) > max_bytes:
            return {"ok": False, "error": "content_too_large", "text": ""}
        
        return {"ok": True, "error": None, "text": text}
    
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"request_error: {type(e).__name__}: {str(e)[:100]}", "text": ""}
    except Exception as e:
        return {"ok": False, "error": f"exception: {type(e).__name__}: {str(e)[:100]}", "text": ""}


def _parse_search_results_v1(html_text: str, max_results: int) -> List[Dict[str, str]]:
    """Original parser with strict class matching."""
    results: List[Dict[str, str]] = []

    snippet_matches = re.findall(
        r'<(?:a|div|td)[^>]*class\s*=\s*["\'][^"\']*(?:result__snippet|result-snippet)[^"\']*["\'][^>]*>(.*?)</(?:a|div|td)>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    snippets: List[str] = [_strip_tags(s) for s in snippet_matches]
    snippet_idx = 0

    anchor_matches = re.finditer(
        r'<a([^>]*)>(.*?)</a>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    for m in anchor_matches:
        if len(results) >= max_results:
            break

        attrs = m.group(1) or ""
        if re.search(r'class\s*=\s*["\'][^"\']*(?:result__a|result-link)[^"\']*["\']', attrs, flags=re.IGNORECASE) is None:
            continue

        href_match = re.search(r'href\s*=\s*["\']([^"\']+)["\']', attrs, flags=re.IGNORECASE)
        if href_match is None:
            continue

        raw_url = html.unescape((href_match.group(1) or "").strip())
        title = _strip_tags(m.group(2) or "")

        if not raw_url or not title:
            continue

        if raw_url.startswith("//"):
            raw_url = "https:" + raw_url

        url = _decode_duckduckgo_redirect(raw_url)
        if not (url.startswith("http://") or url.startswith("https://")):
            continue

        snippet = ""
        if snippet_idx < len(snippets):
            snippet = snippets[snippet_idx]
            snippet_idx += 1

        results.append({
            "title": title,
            "url": url,
            "snippet": snippet,
        })

    return results


def _parse_search_results_v2(html_text: str, max_results: int) -> List[Dict[str, str]]:
    """Fallback: looser parsing, extract results from result-row or tr blocks."""
    results: List[Dict[str, str]] = []

    # Try to extract result blocks (may contain title/URL/snippet together)
    result_block_patterns = [
        (r'<tr[^>]*id\s*=\s*["\']r(\d+)["\'][^>]*>(.*?)</tr>', re.IGNORECASE | re.DOTALL),
        (r'<div[^>]*class\s*=\s*["\']result[^"\']*["\'][^>]*>(.*?)</div>', re.IGNORECASE | re.DOTALL),
    ]

    all_blocks = []
    for pattern, flags in result_block_patterns:
        blocks = re.findall(pattern, html_text, flags=flags)
        if blocks:
            all_blocks = blocks
            break

    if all_blocks:
        for block in all_blocks[:max_results]:
            block_html = block[1] if isinstance(block, tuple) else block
            
            # Extract URL from href in block
            url_match = re.search(r'href\s*=\s*["\']([^"\']+)["\']', block_html, re.IGNORECASE)
            if not url_match:
                continue
            
            raw_url = html.unescape(url_match.group(1).strip())
            
            # Extract title (text inside first <a> tag)
            title_match = re.search(r'<a[^>]*>([^<]+)</a>', block_html, re.IGNORECASE)
            title = _strip_tags(title_match.group(1)) if title_match else ""
            
            # Extract snippet
            snippet_match = re.search(r'<span[^>]*class\s*=\s*["\'][^"\']*snippet[^"\']*["\'][^>]*>(.*?)</span>', block_html, re.IGNORECASE | re.DOTALL)
            snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""
            
            if not raw_url or not title:
                continue

            if raw_url.startswith("//"):
                raw_url = "https:" + raw_url

            url = _decode_duckduckgo_redirect(raw_url)
            if not (url.startswith("http://") or url.startswith("https://")):
                continue

            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
            })

        if results:
            return results

    return []


def _parse_search_results_v3(html_text: str, max_results: int) -> List[Dict[str, str]]:
    """Last resort: extract any href that looks like search result."""
    results: List[Dict[str, str]] = []
    seen_urls = set()

    # Find all links with context
    link_pattern = r'href\s*=\s*["\']([^"\']+)["\'][^>]*>([^<]+)</a>'
    matches = re.finditer(link_pattern, html_text, re.IGNORECASE | re.DOTALL)

    for m in matches:
        if len(results) >= max_results:
            break

        raw_url = html.unescape(m.group(1).strip())
        title = _strip_tags(m.group(2).strip())

        if not raw_url or not title or len(title) < 3:
            continue

        if raw_url.startswith("//"):
            raw_url = "https:" + raw_url

        url = _decode_duckduckgo_redirect(raw_url)
        if not (url.startswith("http://") or url.startswith("https://")):
            continue

        # Filter out common non-result URLs
        if any(x in url.lower() for x in ["duckduckgo.com", "reddit.com/r/", "github.com/search", "site:reddit"]):
            continue

        if url in seen_urls:
            continue

        seen_urls.add(url)
        results.append({
            "title": title,
            "url": url,
            "snippet": "",  # No snippet in fallback
        })

    return results


def _parse_search_results(html_text: str, max_results: int) -> List[Dict[str, str]]:
    """Multi-strategy parser: try best methods first, fall back gracefully."""
    # Try primary parser first
    results = _parse_search_results_v1(html_text, max_results)
    if results and len(results) >= 2:
        return results

    # Try secondary parser
    results = _parse_search_results_v2(html_text, max_results)
    if results and len(results) >= 2:
        return results

    # Try last resort
    results = _parse_search_results_v3(html_text, max_results)
    return results


def _plan_search_queries(query: str) -> List[str]:
    """Generate a small ranked set of query variants for robust retrieval."""
    q = (query or "").strip()
    if not q:
        return []

    variants: List[str] = [q]

    cleaned = re.sub(
        r'^(?:please\s+)?(?:search\s+(?:the\s+)?web\s+for|search\s+online\s+for|find\s+on\s+the\s+web|find\s+online|look\s+up)\s+',
        "",
        q,
        flags=re.IGNORECASE,
    ).strip(" .?!")
    if cleaned and cleaned.lower() != q.lower():
        variants.append(cleaned)

    # Add one conservative rewrite for "latest ... news" style prompts.
    lowered = cleaned.lower() if cleaned else q.lower()
    if "latest" in lowered and "news" in lowered:
        compact = re.sub(r"\blatest\b", "", cleaned or q, flags=re.IGNORECASE)
        compact = re.sub(r"\bnews\b", "", compact, flags=re.IGNORECASE)
        compact = re.sub(r"\s+", " ", compact).strip(" .?!")
        if compact:
            variants.append(f"{compact} news")

    seen = set()
    ordered: List[str] = []
    for v in variants:
        key = v.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(v)
    return ordered[:4]


def _duckduckgo_instant_search(query: str, timeout: int) -> List[Dict[str, str]]:
    """Primary source: DuckDuckGo instant answer API."""
    if HAS_REQUESTS:
        response = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json"},
            timeout=timeout,
            headers={"User-Agent": USER_AGENT}
        )
        response.raise_for_status()
        data = response.json()
    else:
        url = "https://api.duckduckgo.com/?q=" + urllib.parse.quote_plus(query) + "&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8'))

    results: List[Dict[str, str]] = []

    if data.get("AbstractText"):
        results.append({
            "title": data.get("Heading", query),
            "url": data.get("AbstractURL", ""),
            "snippet": data.get("AbstractText", "")
        })

    related_items: List[Dict[str, Any]] = []
    for topic in data.get("RelatedTopics", []):
        if not isinstance(topic, dict):
            continue
        if "Topics" in topic and isinstance(topic.get("Topics"), list):
            for sub in topic.get("Topics", []):
                if isinstance(sub, dict):
                    related_items.append(sub)
            continue
        related_items.append(topic)

    for topic in related_items:
        results.append({
            "title": topic.get("Text", ""),
            "url": topic.get("FirstURL", ""),
            "snippet": ""
        })

    for result in data.get("Results", []):
        results.append({
            "title": result.get("Text", ""),
            "url": result.get("FirstURL", ""),
            "snippet": ""
        })

    return [r for r in results if r.get("title") and r.get("url")]


def _duckduckgo_html_search(query: str, timeout: int, max_results: int) -> List[Dict[str, str]]:
    """Fallback source: DuckDuckGo HTML results page parsed with resilient parsers."""
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)

    page = _fetch_text_url_requests(url, timeout=timeout, max_bytes=MAX_SEARCH_BYTES)
    if not page.get("ok"):
        page = _fetch_text_url(url, timeout=timeout, max_bytes=MAX_SEARCH_BYTES)

    if not page.get("ok"):
        return []

    text = page.get("text") or ""
    if not text.strip():
        return []

    return _parse_search_results(text, max_results)


def _duckduckgo_lite_search(query: str, timeout: int, max_results: int) -> List[Dict[str, str]]:
    """Secondary fallback source: DuckDuckGo lite endpoint."""
    url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote_plus(query)

    page = _fetch_text_url_requests(url, timeout=timeout, max_bytes=MAX_SEARCH_BYTES)
    if not page.get("ok"):
        page = _fetch_text_url(url, timeout=timeout, max_bytes=MAX_SEARCH_BYTES)

    if not page.get("ok"):
        return []

    text = page.get("text") or ""
    if not text.strip():
        return []

    out: List[Dict[str, str]] = []
    seen = set()
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, flags=re.IGNORECASE | re.DOTALL):
        raw_url = str(m.group(1) or "").strip()
        title = _strip_tags(str(m.group(2) or "")).strip()
        if not raw_url or not title:
            continue

        final_url = _decode_duckduckgo_redirect(raw_url)
        if final_url.startswith("/"):
            final_url = "https://lite.duckduckgo.com" + final_url
        if not re.match(r"https?://", final_url, flags=re.IGNORECASE):
            continue
        if "duckduckgo.com" in final_url.lower():
            continue

        key = (title.lower(), final_url.lower())
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "title": title,
            "url": final_url,
            "snippet": "",
        })
        if len(out) >= max_results:
            break

    return out


def _bing_html_search(query: str, timeout: int, max_results: int) -> List[Dict[str, str]]:
    """Last-resort source: parse Bing HTML result rows."""
    url = "https://www.bing.com/search?q=" + urllib.parse.quote_plus(query)

    page = _fetch_text_url_requests(url, timeout=timeout, max_bytes=MAX_SEARCH_BYTES)
    if not page.get("ok"):
        page = _fetch_text_url(url, timeout=timeout, max_bytes=MAX_SEARCH_BYTES)

    if not page.get("ok"):
        return []

    text = page.get("text") or ""
    if not text.strip():
        return []

    out: List[Dict[str, str]] = []
    seen = set()

    blocks = re.findall(
        r'<li[^>]*class=["\'][^"\']*b_algo[^"\']*["\'][^>]*>([\s\S]*?)</li>',
        text,
        flags=re.IGNORECASE,
    )

    for block in blocks:
        m = re.search(r'<h2>\s*<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>\s*</h2>', block, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        url_val = str(m.group(1) or "").strip()
        title = _strip_tags(str(m.group(2) or "")).strip()
        if not url_val or not title:
            continue
        if not re.match(r"https?://", url_val, flags=re.IGNORECASE):
            continue

        snippet_match = re.search(r'<p>([\s\S]*?)</p>', block, flags=re.IGNORECASE)
        snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""

        key = (title.lower(), url_val.lower())
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "title": title,
            "url": url_val,
            "snippet": snippet,
        })
        if len(out) >= max_results:
            break

    # Ultra-light fallback for markup variants: collect <h2><a ...> pairs globally.
    if not out:
        for m in re.finditer(r'<h2>\s*<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>\s*</h2>', text, flags=re.IGNORECASE | re.DOTALL):
            url_val = str(m.group(1) or "").strip()
            title = _strip_tags(str(m.group(2) or "")).strip()
            if not url_val or not title:
                continue
            if not re.match(r"https?://", url_val, flags=re.IGNORECASE):
                continue
            key = (title.lower(), url_val.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "title": title,
                "url": url_val,
                "snippet": "",
            })
            if len(out) >= max_results:
                break

    return out


def _bing_rss_search(query: str, timeout: int, max_results: int) -> List[Dict[str, str]]:
    """Robust fallback: Bing RSS feed results (simple XML payload)."""
    url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote_plus(query)

    page = _fetch_text_url_requests(url, timeout=timeout, max_bytes=MAX_SEARCH_BYTES)
    if not page.get("ok"):
        page = _fetch_text_url(url, timeout=timeout, max_bytes=MAX_SEARCH_BYTES)

    if not page.get("ok"):
        return []

    text = page.get("text") or ""
    if not text.strip():
        return []

    out: List[Dict[str, str]] = []
    seen = set()

    for item in re.findall(r"<item>([\s\S]*?)</item>", text, flags=re.IGNORECASE):
        title_m = re.search(r"<title>([\s\S]*?)</title>", item, flags=re.IGNORECASE)
        link_m = re.search(r"<link>([\s\S]*?)</link>", item, flags=re.IGNORECASE)
        desc_m = re.search(r"<description>([\s\S]*?)</description>", item, flags=re.IGNORECASE)

        title = html.unescape(_strip_tags(title_m.group(1))) if title_m else ""
        url_val = html.unescape((link_m.group(1) or "").strip()) if link_m else ""
        snippet = html.unescape(_strip_tags(desc_m.group(1))) if desc_m else ""

        title = title.strip()
        url_val = url_val.strip()
        snippet = snippet.strip()

        if not title or not url_val:
            continue
        if not re.match(r"https?://", url_val, flags=re.IGNORECASE):
            continue

        key = (title.lower(), url_val.lower())
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "title": title,
            "url": url_val,
            "snippet": snippet,
        })
        if len(out) >= max_results:
            break

    return out


def _get_weather(location: str, timeout: int = 10) -> List[Dict[str, str]]:
    """Fetch weather data from wttr.in (bot-friendly)."""
    try:
        if HAS_REQUESTS:
            url = f"https://wttr.in/{urllib.parse.quote(location)}?format=j1"
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            
            # Extract current conditions and forecast
            results = []
            try:
                current = data.get('current_condition', [{}])[0]
                forecast = data.get('weather', [{}])[0]
                
                current_temp = current.get('temp_C', 'N/A')
                description = current.get('weatherDesc', [{}])[0].get('value', 'Unknown')
                humidity = current.get('humidity', 'N/A')
                wind = current.get('windspeedKmph', 'N/A')
                
                results.append({
                    "title": f"Current Weather in {location.title()}",
                    "snippet": f"Temperature: {current_temp}°C, {description}, Humidity: {humidity}%, Wind: {wind} km/h",
                    "url": f"https://wttr.in/{urllib.parse.quote(location)}",
                    "source": "wttr.in"
                })
                
                # Add forecast if available
                if forecast and 'date' in forecast:
                    forecast_days = forecast.get('hourly', [])[:3]  # Next 3 hours
                    if forecast_days:
                        forecast_text = "Forecast: " + ", ".join([
                            f"{day.get('time', 'N/A')}: {day.get('weatherDesc', [{}])[0].get('value', 'N/A')} ({day.get('temp_C', 'N/A')}°C)"
                            for day in forecast_days if day
                        ])
                        results.append({
                            "title": f"Weather Forecast for {location.title()}",
                            "snippet": forecast_text,
                            "url": f"https://wttr.in/{urllib.parse.quote(location)}",
                            "source": "wttr.in"
                        })
                
            except (KeyError, IndexError, TypeError):
                pass
            
            return results if results else []
        else:
            # Fallback without requests
            url = f"https://wttr.in/{urllib.parse.quote(location)}?format=3"
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode('utf-8', errors='ignore').strip()
                if text:
                    return [{
                        "title": f"Weather in {location.title()}",
                        "snippet": text,
                        "url": f"https://wttr.in/{urllib.parse.quote(location)}",
                        "source": "wttr.in"
                    }]
    except Exception:
        pass
    
    return []


def web_search_impl(
    query: str,
    max_results: int = 5,
    timeout: int = 12,
    include_content: bool = False,
    content_chars: int = 800,
    mode: str = "balanced",
) -> Dict[str, Any]:
    if not query or not isinstance(query, str):
        return {"ok": False, "result": None, "error": "no_query_provided"}

    if max_results < 1 or max_results > 10:
        return {"ok": False, "result": None, "error": "max_results_out_of_range_1_to_10"}

    if timeout < 1 or timeout > 30:
        return {"ok": False, "result": None, "error": "timeout_out_of_range_1_to_30"}

    if content_chars < 200 or content_chars > 4000:
        return {"ok": False, "result": None, "error": "content_chars_out_of_range_200_to_4000"}

    mode_value = str(mode or "balanced").strip().lower()
    if mode_value not in SEARCH_MODES:
        return {"ok": False, "result": None, "error": "mode_must_be_balanced_strict_or_wide"}

    # Route weather queries to wttr.in first so weather stays deterministic.
    q_low = query.strip().lower()
    if "weather" in q_low:
        location = query
        m = re.search(r"\bweather(?:\s+(?:in|for|at))?\s+(.+)$", query, re.IGNORECASE)
        if m:
            location = m.group(1).strip(" ?.!")
        location = location or "current location"

        weather_results = _get_weather(location, timeout=timeout)
        return {
            "ok": True,
            "result": {
                "results": weather_results[:max_results],
                "count": len(weather_results[:max_results]),
                "query": query,
            },
            "error": None,
        }

    topic = _detect_topic(query)

    # Adaptive source/query planning:
    # 1) Try instant-answer API (fast structured source).
    # 2) Fall back to DuckDuckGo HTML results parsing.
    # 3) Retry with small query rewrites if needed.
    try:
        planned_queries = _plan_search_queries(query)
        if not planned_queries:
            planned_queries = [query]

        results: List[Dict[str, str]] = []
        used_query = query
        used_source = "none"

        for q_try in planned_queries:
            instant_results = _duckduckgo_instant_search(q_try, timeout=timeout)
            if instant_results:
                results = instant_results[:max_results]
                used_query = q_try
                used_source = "duckduckgo_instant"
                break

            html_results = _duckduckgo_html_search(q_try, timeout=timeout, max_results=max_results)
            if html_results:
                results = html_results[:max_results]
                used_query = q_try
                used_source = "duckduckgo_html"
                break

            lite_results = _duckduckgo_lite_search(q_try, timeout=timeout, max_results=max_results)
            if lite_results:
                results = lite_results[:max_results]
                used_query = q_try
                used_source = "duckduckgo_lite"
                break

            rss_results = _bing_rss_search(q_try, timeout=timeout, max_results=max_results)
            if rss_results:
                results = rss_results[:max_results]
                used_query = q_try
                used_source = "bing_rss"
                break

            bing_results = _bing_html_search(q_try, timeout=timeout, max_results=max_results)
            if bing_results:
                results = bing_results[:max_results]
                used_query = q_try
                used_source = "bing_html"
                break

        if not results:
            return {
                "ok": True,
                "result": {
                    "results": [],
                    "count": 0,
                    "query": query,
                    "used_query": query,
                    "source": "none",
                    "tried_queries": planned_queries,
                    "mode": mode_value,
                    "topic": topic,
                },
                "error": None
            }

        ranked = _rerank_results(results, query=query, topic=topic, mode=mode_value, max_results=max_results)
        if ranked:
            results = ranked
        else:
            # As a safety valve, avoid empty output due to strict filtering.
            results = results[:max_results]
        
        # Optionally fetch content from URLs
        if include_content:
            for item in results:
                page = _fetch_text_url_requests(item["url"], timeout=timeout, max_bytes=MAX_PAGE_BYTES)
                if not page.get("ok"):
                    page = _fetch_text_url(item["url"], timeout=timeout, max_bytes=MAX_PAGE_BYTES)
                if page.get("ok"):
                    text = _strip_tags(page["text"])
                    if len(text) > content_chars:
                        text = text[:content_chars].rstrip() + "..."
                    item["content_excerpt"] = text
                else:
                    item["content_error"] = page.get("error")
        
        return {
            "ok": True,
            "result": {
                "results": results,
                "count": len(results),
                "query": query,
                "used_query": used_query,
                "source": used_source,
                "tried_queries": planned_queries,
                "mode": mode_value,
                "topic": topic,
            },
            "error": None
        }
    
    except Exception as e:
        return {
            "ok": False,
            "result": None,
            "error": f"{type(e).__name__}: {str(e)[:100]}"
        }


def tool_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("query") or "").strip()

    try:
        max_results = int(args.get("max_results", 5) or 5)
    except Exception:
        return {"ok": False, "result": None, "error": "invalid_max_results_type"}

    try:
        timeout = int(args.get("timeout", 12) or 12)
    except Exception:
        return {"ok": False, "result": None, "error": "invalid_timeout_type"}

    include_content_raw = args.get("include_content", False)
    include_content = bool(include_content_raw)

    try:
        content_chars = int(args.get("content_chars", 800) or 800)
    except Exception:
        return {"ok": False, "result": None, "error": "invalid_content_chars_type"}

    mode = str(args.get("mode", "balanced") or "balanced").strip().lower()

    return web_search_impl(
        query=query,
        max_results=max_results,
        timeout=timeout,
        include_content=include_content,
        content_chars=content_chars,
        mode=mode,
    )


tool_entry.schema = {
    "description": "Search the public web and return result titles, URLs, snippets, and optional fetched content excerpts.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query text.",
            },
            "max_results": {
                "type": "integer",
                "description": "Max number of search results (1-10).",
                "minimum": 1,
                "maximum": 10,
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (1-30).",
                "minimum": 1,
                "maximum": 30,
            },
            "include_content": {
                "type": "boolean",
                "description": "When true, fetch each result page and return a text excerpt.",
            },
            "content_chars": {
                "type": "integer",
                "description": "Max characters in each fetched content excerpt (200-4000).",
                "minimum": 200,
                "maximum": 4000,
            },
            "mode": {
                "type": "string",
                "description": "Search quality mode: balanced (default), strict (cleaner), wide (broader recall).",
                "enum": ["balanced", "strict", "wide"],
            },
        },
        "required": ["query"],
    },
}
