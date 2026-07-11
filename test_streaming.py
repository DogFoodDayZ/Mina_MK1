#!/usr/bin/env python3
"""
Test the streaming /process-stream endpoint.
Demonstrates real-time chunk arrival.
"""
import json
import requests
import time

BASE_URL = "http://127.0.0.1:8000/process-stream"

def test_streaming(prompt: str) -> None:
    """Test streaming endpoint and display chunks in real-time."""
    print(f"\n{'='*70}")
    print(f"STREAMING TEST: {prompt[:50]}...")
    print(f"{'='*70}")
    print("Response chunks (real-time):\n")
    
    try:
        body = {"input": prompt}
        response = requests.post(
            BASE_URL,
            json=body,
            stream=True,
            timeout=120,
        )
        response.raise_for_status()
        
        start_time = time.time()
        chunk_count = 0
        full_response = ""
        
        for line in response.iter_lines():
            if not line:
                continue
            
            line_str = line.decode("utf-8") if isinstance(line, bytes) else line
            if not line_str.startswith("data: "):
                continue
            
            try:
                data = json.loads(line_str[6:])
                msg_type = data.get("type")
                
                if msg_type == "chunk":
                    chunk = data.get("content", "")
                    full_response += chunk
                    print(chunk, end="", flush=True)
                    chunk_count += 1
                    
                elif msg_type == "done":
                    elapsed = time.time() - start_time
                    print(f"\n\n✅ Streaming complete!")
                    print(f"   Chunks received: {chunk_count}")
                    print(f"   Total time: {elapsed:.1f}s")
                    print(f"   Response length: {len(full_response)} chars")
                    break
                    
                elif msg_type == "error":
                    error = data.get("error", "Unknown error")
                    print(f"\n\n❌ Error: {error}")
                    break
                    
            except json.JSONDecodeError:
                continue
        
    except Exception as e:
        print(f"❌ Connection error: {e}")

# Test cases
tests = [
    "write a quick hello world in python",
    "what's the capital of france",
    "generate fibonacci code",
]

for test in tests:
    test_streaming(test)
    time.sleep(1)

print(f"\n{'='*70}")
print("All streaming tests completed!")
