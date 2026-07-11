#!/usr/bin/env python3
"""Simple streaming test"""
import json
import urllib.request
import time

BASE_URL = "http://127.0.0.1:8000/process-stream"

print("Testing /process-stream endpoint...")
print("=" * 60)

body = {"input": "write hello world in python"}
req = urllib.request.Request(
    BASE_URL,
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    print("Sending request...")
    resp = urllib.request.urlopen(req, timeout=30)
    print(f"Response status: {resp.status}")
    print(f"Response headers: {dict(resp.headers)}")
    print("\nStreaming chunks:")
    print("-" * 60)
    
    chunk_count = 0
    for line in resp:
        line_str = line.decode("utf-8").strip()
        if line_str.startswith("data: "):
            try:
                data = json.loads(line_str[6:])
                if data.get("type") == "chunk":
                    print(data.get("content", ""), end="", flush=True)
                    chunk_count += 1
                elif data.get("type") == "done":
                    print("\n\n✓ Streaming complete!")
                    break
            except:
                pass
    
    print(f"Total chunks: {chunk_count}")

except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()
