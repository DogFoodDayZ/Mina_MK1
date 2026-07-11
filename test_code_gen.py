#!/usr/bin/env python3
"""Test code generation feature."""
import json
import time
import urllib.request

time.sleep(1)

print("=" * 60)
print("CODE GENERATION TEST")
print("=" * 60)
print("Requesting model to generate Python code...")
print("(This may take 10-30 seconds - waiting for LMStudio model)\n")

body = {"input": "write a python script that finds the factorial of a number"}
req = urllib.request.Request(
    "http://127.0.0.1:8000/process",
    data=json.dumps(body).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        reply = result.get("reply", "")
        # Show first 500 chars and look for code block
        print(reply[:800])
        if "```" in reply:
            print("\n✓ Code block detected in response!")
except urllib.error.URLError as e:
    if "timed out" in str(e).lower():
        print("⚠️  Request timed out - model may be slow or busy")
    else:
        print(f"Error: {e}")
except Exception as e:
    print(f"Error: {e}")
