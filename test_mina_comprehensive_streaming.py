#!/usr/bin/env python3
"""
Comprehensive test of Mina MK1 with streaming for slow operations.
Uses /process for fast tools, /process-stream for generation tasks.
"""
import json
import time
import urllib.request
import urllib.error

BASE_PROCESS_URL = "http://127.0.0.1:8000/process"
BASE_STREAM_URL = "http://127.0.0.1:8000/process-stream"

def test_mina_sync(input_text: str, test_name: str, timeout: int = 30) -> str:
    """Call Mina via /process endpoint (fast operations only)."""
    print(f"\n{'='*70}")
    print(f"TEST: {test_name}")
    print(f"{'='*70}")
    print(f"Input: {input_text}")
    print(f"-" * 70)
    
    try:
        body = {"input": input_text}
        req = urllib.request.Request(
            BASE_PROCESS_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            reply = result.get("reply", "")
            
            if len(reply) > 600:
                print(reply[:600])
                print(f"\n... [truncated, total {len(reply)} chars]")
            else:
                print(reply)
            
            return reply
    except urllib.error.URLError as e:
        if "timed out" in str(e).lower():
            print(f"[TIMEOUT] Request exceeded {timeout}s")
            return ""
        else:
            print(f"[ERROR] {e}")
            return ""
    except Exception as e:
        print(f"[ERROR] {e}")
        return ""

def test_mina_stream(input_text: str, test_name: str, timeout: int = 120) -> str:
    """Call Mina via /process-stream endpoint (streaming generation tasks)."""
    print(f"\n{'='*70}")
    print(f"TEST: {test_name} (STREAMING)")
    print(f"{'='*70}")
    print(f"Input: {input_text}")
    print(f"-" * 70)
    print("Response (streaming chunks):\n")
    
    try:
        body = {"input": input_text}
        req = urllib.request.Request(
            BASE_STREAM_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        start_time = time.time()
        full_response = ""
        chunk_count = 0
        
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for line in resp:
                line_str = line.decode("utf-8").strip()
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
                        print(f"\n\n[COMPLETE] Streaming complete ({chunk_count} chunks, {elapsed:.1f}s)")
                        break
                        
                except json.JSONDecodeError:
                    continue
        
        return full_response
        
    except urllib.error.URLError as e:
        if "timed out" in str(e).lower():
            print(f"[TIMEOUT] Stream exceeded {timeout}s")
            return ""
        else:
            print(f"[ERROR] {e}")
            return ""
    except Exception as e:
        print(f"[ERROR] {e}")
        return ""

# Warm up the API
print("[*] Warming up Mina (2s delay)...")
time.sleep(2)

tests = [
    # =========================================================================
    # 1. MEMORY SYSTEM (sync - fast)
    # =========================================================================
    ("remember that my favorite color is purple and i love coding", "MEMORY: Store fact", "sync", 30),
    ("what do you know about me", "MEMORY: Recall fact", "sync", 30),
    
    # =========================================================================
    # 2. WEB SEARCH (sync - fast with hardened parser)
    # =========================================================================
    ("search the web for latest developments in ai agents 2026", "WEB SEARCH: Query with results", "sync", 45),
    
    # =========================================================================
    # 3. CODE EXECUTION - Python (sync - very fast)
    # =========================================================================
    ("execute this code: x = [i**2 for i in range(1, 6)]; print(f'Squares: {x}')", "CODE EXEC: Python list comprehension", "sync", 30),
    
    # =========================================================================
    # 4. CODE EXECUTION - PowerShell (sync - very fast)
    # =========================================================================
    ("run this powershell: $env:COMPUTERNAME", "CODE EXEC: PowerShell environment", "sync", 30),
    
    # =========================================================================
    # 5. FILE OPERATIONS (sync - fast)
    # =========================================================================
    ("create a folder called test_mina_features", "FILE OPS: Create directory", "sync", 30),
    ("write to file test_mina_features\\hello.txt: Hello from Mina! This is a test file created on {0}".format(time.strftime("%Y-%m-%d %H:%M:%S")), "FILE OPS: Write file", "stream", 90),
    ("read the file test_mina_features\\hello.txt", "FILE OPS: Read file", "sync", 30),
    
    # =========================================================================
    # 6. CODE GENERATION (STREAM - slow, model-based)
    # =========================================================================
    ("write me a python script that generates fibonacci numbers up to n", "CODE GEN: Generate Fibonacci", "stream", 120),
    
    # =========================================================================
    # 7. SYSTEM INFO (sync - fast)
    # =========================================================================
    ("what system am i on", "SYSTEM INFO: Platform and specs", "sync", 30),
    
    # =========================================================================
    # 8. ERROR HANDLING (sync - fast)
    # =========================================================================
    ("execute this code: result = 10 / 0", "ERROR HANDLING: Python exception", "sync", 30),
    
    # =========================================================================
    # 9. PERSONALITY TEST (STREAM - slow, model-based)
    # =========================================================================
    ("tell me a joke about debugging", "PERSONALITY: Mina's attitude", "stream", 120),
]

passed = 0
failed = 0

for input_text, test_name, mode, timeout in tests:
    if mode == "sync":
        reply = test_mina_sync(input_text, test_name, timeout=timeout)
    else:  # stream
        reply = test_mina_stream(input_text, test_name, timeout=timeout)
    
    if reply:
        print("[PASS]")
        passed += 1
    else:
        print("[FAIL]")
        failed += 1
    
    time.sleep(1)

# =========================================================================
# SUMMARY
# =========================================================================
print(f"\n{'='*70}")
print("[RESULTS] TEST SUMMARY")
print(f"{'='*70}")
print(f"PASSED: {passed}/13 [OK]")
print(f"FAILED: {failed}/13")
print(f"\n[STRATEGY]")
print(f"   * Fast tools (/process): Memory, Web, Code exec, File ops (30-60s)")
print(f"   * Slow generation (/process-stream): Code gen, Creative responses (streams chunks)")
print(f"   * NO MORE TIMEOUTS: Streaming shows output as it arrives!")
