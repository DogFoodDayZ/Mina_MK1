#!/usr/bin/env python3
"""Test file write directly."""
import json
import time
import urllib.request

time.sleep(2)

# Test simpler file write
body = {"input": "write to file test.txt: hello world"}
req = urllib.request.Request(
    "http://127.0.0.1:8000/process",
    data=json.dumps(body).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        reply = result.get("reply", "")
        print("File write response:")
        print(reply[:500])
except Exception as e:
    print(f"Error: {e}")
