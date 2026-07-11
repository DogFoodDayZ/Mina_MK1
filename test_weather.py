from tools.web_search import _get_weather

# Test weather query
result = _get_weather("spokane WA", timeout=15)
print(f"Found {len(result)} weather results")
for r in result:
    print(f"Title: {r.get('title', '')}")
    snippet = r.get("snippet", "")[:80]
    print(f"Snippet: {snippet}...")
    print()
