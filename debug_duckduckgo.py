import urllib.parse
from tools.web_search import _fetch_text_url, MAX_SEARCH_BYTES

query = 'python'
url = 'https://lite.duckduckgo.com/lite/?q=' + urllib.parse.quote_plus(query)
fetched = _fetch_text_url(url, timeout=15, max_bytes=MAX_SEARCH_BYTES)
html = fetched['text']

# Print first 3000 chars to see HTML structure
print("===== FIRST 3000 CHARS OF RESPONSE =====")
print(html[:3000])
print("\n===== ANALYZING STRUCTURE =====")

# Look for key structural patterns
if '<table' in html:
    print("✓ Found <table> elements")
if '<tr' in html:
    print("✓ Found <tr> elements")
if '<td' in html:
    print("✓ Found <td> elements")
if 'javascript:doForm' in html:
    print("✓ Found javascript:doForm pattern")

import re
# Try to extract anything that looks like a result
h2_tags = re.findall(r'<h2[^>]*>(.*?)</h2>', html)
print(f"\nFound {len(h2_tags)} <h2> tags")
if h2_tags:
    print("First h2:", h2_tags[0][:100])

# Try divs
div_blocks = re.findall(r'<div[^>]*class="[^"]*result[^"]*"[^>]*>(.*?)</div>', html, re.IGNORECASE)
print(f"Found {len(div_blocks)} result divs")
if div_blocks:
    print("First div:", div_blocks[0][:100])
