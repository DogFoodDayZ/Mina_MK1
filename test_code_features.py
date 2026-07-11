#!/usr/bin/env python3
"""Test code execution and generation features."""
import json
import time
import urllib.request
import urllib.error

# Add small delay for API startup
time.sleep(2)

# Test 1: Code execution
print("=" * 50)
print("TEST 1: Code execution via /process endpoint")
print("=" * 50)

body1 = {"input": "execute this code: print('Hello from Mina'); print('2 + 2 =', 2 + 2)"}
req = urllib.request.Request(
    "http://127.0.0.1:8000/process",
    data=json.dumps(body1).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        reply = result.get("reply", "")
        print(f"Reply:\n{reply[:500]}")
        print(f"... (total {len(reply)} chars)")
except Exception as e:
    print(f"Error: {e}")

# Test 2: Code generation
print("\n" + "=" * 50)
print("TEST 2: Code generation request")
print("=" * 50)

body2 = {"input": "write a python script that calculates factorial with error handling"}
req2 = urllib.request.Request(
    "http://127.0.0.1:8000/process",
    data=json.dumps(body2).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    with urllib.request.urlopen(req2, timeout=30) as resp:
        result2 = json.loads(resp.read().decode("utf-8"))
        reply2 = result2.get("reply", "")
        print(f"Reply:\n{reply2[:500]}")
        print(f"... (total {len(reply2)} chars)")
except Exception as e:
    print(f"Error: {e}")

# Test 3: Python code with timeout
print("\n" + "=" * 50)
print("TEST 3: Inline Python execution")
print("=" * 50)

body3 = {"input": "run this python: x = [i**2 for i in range(5)]; print('Squares:', x)"}
req3 = urllib.request.Request(
    "http://127.0.0.1:8000/process",
    data=json.dumps(body3).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    with urllib.request.urlopen(req3, timeout=30) as resp:
        result3 = json.loads(resp.read().decode("utf-8"))
        reply3 = result3.get("reply", "")
        print(f"Reply:\n{reply3}")
except Exception as e:
    print(f"Error: {e}")
