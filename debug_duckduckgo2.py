import urllib.parse
from tools.web_search import _fetch_text_url, MAX_SEARCH_BYTES
import re

query = 'python'
url = 'https://lite.duckduckgo.com/lite/?q=' + urllib.parse.quote_plus(query)
fetched = _fetch_text_url(url, timeout=15, max_bytes=MAX_SEARCH_BYTES)
html = fetched['text']

# Look for result rows which should be after the form
# DuckDuckGo lite uses table-based layout with tr/td

# Find all href patterns that look like DDG redirect links
redirects = re.findall(r'href="(/l/\?.*?)"', html)
print(f"Found {len(redirects)} DDG redirect links")
if redirects:
    print("First 3 redirects:")
    for r in redirects[:3]:
        print(f"  {r[:100]}")

# Try finding actual search result text patterns
# Look for anything between <a> tags that might be result titles
a_text = re.findall(r'<a[^>]*href="[^"]*"[^>]*>([^<]+)</a>', html)
print(f"\nFound {len(a_text)} link texts")
if a_text:
    print("First 5 link texts:")
    for i, t in enumerate(a_text[:5]):
        clean = t.strip()[:80]
        print(f"  {i+1}. {clean}")

# Check if we're getting bot protection
if 'challenge' in html.lower() or 'verify' in html.lower():
    print("\n⚠️ DuckDuckGo appears to be showing bot challenge/verification page")
    
# Look for actual result patterns - DuckDuckGo lite uses table rows
rows = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL)
print(f"\nFound {len(rows)} table rows")

# Check for any JavaScript that might indicate dynamic loading
if 'script' in html.lower():
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    print(f"Found {len(scripts)} script blocks")
    # Show if any have actual code (not just analytics)
    for s in scripts:
        if len(s) > 50 and 'duckduckgo' not in s.lower():
            print(f"  Non-trivial script found: {s[:100]}...")

# Check HTML size and structure
print(f"\nTotal HTML size: {len(html)} bytes")
print(f"Contains 'result': {'result' in html.lower()}")
print(f"Contains 'python': {'python' in html.lower()}")
print(f"Contains 'href': {'href' in html}")
