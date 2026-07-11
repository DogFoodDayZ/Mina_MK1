#!/usr/bin/env python3
"""Test code_execute tool."""
from tools.code_execute import tool_entry
import json

# Test 1: Simple Python
print("=" * 50)
print("TEST 1: Simple Python code")
print("=" * 50)
r = tool_entry({
    'code': 'print("Hello from Mina"); import math; print(f"pi = {math.pi:.4f}")',
})
print(json.dumps({k: v for k, v in r.items() if k in ['ok', 'exit_code', 'stdout', 'stderr']}, indent=2))

# Test 2: Python with variables
print("\n" + "=" * 50)
print("TEST 2: Python with list operations")
print("=" * 50)
r = tool_entry({
    'code': 'nums = [1, 2, 3, 4, 5]; print(f"Sum: {sum(nums)}"); print(f"Average: {sum(nums)/len(nums)}")',
})
print(json.dumps({k: v for k, v in r.items() if k in ['ok', 'exit_code', 'stdout', 'stderr']}, indent=2))

# Test 3: PowerShell
print("\n" + "=" * 50)
print("TEST 3: PowerShell code")
print("=" * 50)
r = tool_entry({
    'code': 'Get-Date -Format "yyyy-MM-dd HH:mm:ss"',
    'language': 'powershell',
})
print(json.dumps({k: v for k, v in r.items() if k in ['ok', 'exit_code', 'stdout', 'stderr']}, indent=2))

# Test 4: Error handling
print("\n" + "=" * 50)
print("TEST 4: Python error (catches exception)")
print("=" * 50)
r = tool_entry({
    'code': 'x = 1 / 0  # Divide by zero',
})
print(json.dumps({k: v for k, v in r.items() if k in ['ok', 'exit_code', 'stdout', 'stderr']}, indent=2))

# Test 5: Timeout
print("\n" + "=" * 50)
print("TEST 5: Timeout (sleep 5s with 2s timeout)")
print("=" * 50)
r = tool_entry({
    'code': 'import time; time.sleep(5); print("Done")',
    'timeout': 2,
})
print(json.dumps({k: v for k, v in r.items() if k in ['ok', 'exit_code', 'stdout', 'stderr', 'error']}, indent=2))

# Test 6: Dangerous pattern detection
print("\n" + "=" * 50)
print("TEST 6: Dangerous pattern warning")
print("=" * 50)
r = tool_entry({
    'code': 'import os; print("Code with os.system")',
    'check_dangerous': True,
})
print(f"Warning: {r.get('warning')}")
print(f"Stdout: {r.get('stdout')}")
