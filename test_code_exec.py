#!/usr/bin/env python3
"""Test code execution feature."""
import json
import time
import urllib.request

time.sleep(2)

print("=" * 60)
print("TEST 1: Code execution with colon")
print("=" * 60)

body = {"input": "execute this code: print('Hello from Mina'); print('2+2 =', 2+2)"}
req = urllib.request.Request(
    "http://127.0.0.1:8000/process",
    data=json.dumps(body).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        print(result.get("reply", ""))
except Exception as e:
    print(f"Error: {e}")

print("\n" + "=" * 60)
print("TEST 2: Run python")
print("=" * 60)

body2 = {"input": "run this python: import datetime; print('Today:', datetime.date.today())"}
req2 = urllib.request.Request(
    "http://127.0.0.1:8000/process",
    data=json.dumps(body2).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    with urllib.request.urlopen(req2, timeout=15) as resp:
        result2 = json.loads(resp.read().decode("utf-8"))
        print(result2.get("reply", ""))
except Exception as e:
    print(f"Error: {e}")

print("\n" + "=" * 60)
print("TEST 3: PowerShell execution")
print("=" * 60)

body3 = {"input": "run this powershell: Get-Date -Format 'yyyy-MM-dd HH:mm:ss'"}
req3 = urllib.request.Request(
    "http://127.0.0.1:8000/process",
    data=json.dumps(body3).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    with urllib.request.urlopen(req3, timeout=15) as resp:
        result3 = json.loads(resp.read().decode("utf-8"))
        print(result3.get("reply", ""))
except Exception as e:
    print(f"Error: {e}")

print("\n" + "=" * 60)
print("TEST 4: Error handling (division by zero)")
print("=" * 60)

body4 = {"input": "execute this code: x = 1 / 0"}
req4 = urllib.request.Request(
    "http://127.0.0.1:8000/process",
    data=json.dumps(body4).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    with urllib.request.urlopen(req4, timeout=15) as resp:
        result4 = json.loads(resp.read().decode("utf-8"))
        reply = result4.get("reply", "")
        print(reply[:300])
except Exception as e:
    print(f"Error: {e}")
