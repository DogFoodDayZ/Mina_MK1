#!/usr/bin/env python3
"""Test alternative search engines for bot protection."""

import urllib.parse
import urllib.request

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

engines = {
    "duckduckgo_lite": "https://lite.duckduckgo.com/lite/?q=",
    "google": "https://www.google.com/search?q=",
    "bing": "https://www.bing.com/search?q=",
    "startpage": "https://www.startpage.com/sp/search?query=",
}

query = "python programming"

for name, base_url in engines.items():
    url = base_url + urllib.parse.quote_plus(query)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=5) as r:
            html = r.read(5000).decode('utf-8', errors='ignore')
            
            # Check for challenge/verify pages
            has_challenge = 'challenge' in html.lower() or 'verify' in html.lower() or 'robot' in html.lower()
            has_results = 'href=' in html and len(html) > 2000
            
            print(f"{name:20} OK - Size: {len(html):5} | Has results: {has_results} | Challenge: {has_challenge}")
    except Exception as e:
        print(f"{name:20} ERROR - {type(e).__name__}: {str(e)[:50]}")
