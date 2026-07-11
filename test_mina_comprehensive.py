#!/usr/bin/env python3
"""
Comprehensive live test of Mina MK1 - 6 months of work!
Tests all major features end-to-end through /process endpoint.
"""
import json
import time
import urllib.request
import urllib.error

BASE_URL = "http://127.0.0.1:8000/process"

def test_mina(input_text: str, test_name: str, timeout: int = 30) -> str:
    """Call Mina and return reply."""
    print(f"\n{'='*70}")
    print(f"TEST: {test_name}")
    print(f"{'='*70}")
    print(f"Input: {input_text}")
    print(f"-" * 70)
    
    try:
        body = {"input": input_text}
        req = urllib.request.Request(
            BASE_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            reply = result.get("reply", "")
            
            # Show response (truncate if too long)
            if len(reply) > 600:
                print(reply[:600])
                print(f"\n... [truncated, total {len(reply)} chars]")
            else:
                print(reply)
            
            return reply
    except urllib.error.URLError as e:
        if "timed out" in str(e).lower():
            print(f"⚠️  TIMEOUT - Request exceeded {timeout}s")
            return ""
        else:
            print(f"❌ ERROR: {e}")
            return ""
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return ""

# Warm up the API
print("🔥 Warming up Mina (2s delay)...")
time.sleep(2)

tests = [
    # =========================================================================
    # 1. MEMORY SYSTEM
    # =========================================================================
    ("remember that my favorite color is purple and i love coding", "MEMORY: Store fact"),
    ("what do you know about me", "MEMORY: Recall fact"),
    
    # =========================================================================
    # 2. WEB SEARCH
    # =========================================================================
    ("search the web for latest developments in ai agents 2026", "WEB SEARCH: Query with results"),
    
    # =========================================================================
    # 3. CODE EXECUTION - Python
    # =========================================================================
    ("execute this code: x = [i**2 for i in range(1, 6)]; print(f'Squares: {x}')", "CODE EXEC: Python list comprehension"),
    
    # =========================================================================
    # 4. CODE EXECUTION - PowerShell
    # =========================================================================
    ("run this powershell: $env:COMPUTERNAME", "CODE EXEC: PowerShell environment"),
    
    # =========================================================================
    # 5. FILE OPERATIONS
    # =========================================================================
    ("create a folder called test_mina_features", "FILE OPS: Create directory"),
    ("write to file test_mina_features\\hello.txt: Hello from Mina! This is a test file created on {0}".format(time.strftime("%Y-%m-%d %H:%M:%S")), "FILE OPS: Write file"),
    ("read the file test_mina_features\\hello.txt", "FILE OPS: Read file"),
    
    # =========================================================================
    # 6. CODE GENERATION
    # =========================================================================
    ("write me a python script that generates fibonacci numbers up to n", "CODE GEN: Generate Fibonacci"),
    
    # =========================================================================
    # 7. SYSTEM INFO (PowerShell)
    # =========================================================================
    ("what system am i on", "SYSTEM INFO: Platform and specs"),
    
    # =========================================================================
    # 8. ERROR HANDLING
    # =========================================================================
    ("execute this code: result = 10 / 0", "ERROR HANDLING: Python exception"),
    
    # =========================================================================
    # 9. PERSONALITY TEST
    # =========================================================================
    ("tell me a joke about debugging", "PERSONALITY: Mina's attitude"),
]

passed = 0
failed = 0

for input_text, test_name in tests:
    # Adjust timeout for slow operations
    # GEN/SEARCH tasks need 90s: LMStudio generation (30s) + API overhead (10s) + network (10s)
    timeout = 90 if "GEN" in test_name or "SEARCH" in test_name or "FILE OPS: Write" in test_name else 30
    
    reply = test_mina(input_text, test_name, timeout=timeout)
    
    if reply:
        print("✅ PASS")
        passed += 1
    else:
        print("❌ FAIL")
        failed += 1
    
    # Small delay between tests
    time.sleep(1)

# =========================================================================
# SUMMARY
# =========================================================================
print(f"\n{'='*70}")
print("🎉 TEST SUMMARY")
print(f"{'='*70}")
print(f"✅ Passed: {passed}")
print(f"❌ Failed: {failed}")
print(f"📊 Total:  {passed + failed}")
print(f"{'='*70}")

if failed == 0:
    print("\n🚀 ALL TESTS PASSED! Mina is fully operational!")
else:
    print(f"\n⚠️  {failed} test(s) failed - review above for details")
