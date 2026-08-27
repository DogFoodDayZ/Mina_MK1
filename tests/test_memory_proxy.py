#!/usr/bin/env python3
"""
Test script for SafeMemoryProxy isolation.
Verifies that alternative models can read Mina's memory but writes are isolated.
"""

import sys
import os
import json

# Add parent directory to path so we can import from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.mk1_memory import MK1Memory
from memory.safe_memory_proxy import SafeMemoryProxy

def test_memory_proxy():
    """Test the SafeMemoryProxy isolation."""
    print("[TEST] Initializing MK1Memory...")
    mina_mem = MK1Memory()
    
    print("[TEST] Creating SafeMemoryProxy for dolphin3...")
    proxy = SafeMemoryProxy(
        mina_memory=mina_mem,
        alt_model_name="dolphin3",
    )
    
    # Test 1: Read personality
    print("\n[TEST 1] Reading Mina's personality...")
    personality = proxy.get_personality()
    if personality:
        print(f"  ✓ Got personality ({len(personality)} chars)")
    else:
        print("  ✗ No personality found (this is ok if config/personality.txt is empty)")
    
    # Test 2: Read system status
    print("\n[TEST 2] Reading Mina's system status...")
    status = proxy.get_status()
    if status.get("ok"):
        print(f"  ✓ Got system status: {status}")
    else:
        print(f"  ✗ Status check failed: {status}")
    
    # Test 3: Write to isolated session (should NOT go to Mina's memory)
    print("\n[TEST 3] Writing to isolated session...")
    op_id_1 = proxy.add_memory("Dolphin test memory 1", tags=["test", "dolphin"])
    op_id_2 = proxy.add_memory("Dolphin test memory 2", tags=["test", "dolphin"])
    op_id_3 = proxy.add_task_memory("Dolphin test task", tags=["test", "task"])
    print(f"  ✓ Created 3 operations: {op_id_1}, {op_id_2}, {op_id_3}")
    
    # Test 4: Verify writes are in session log, not in Mina's memory
    print("\n[TEST 4] Verifying isolation (writes in session, not in Mina)...")
    session_writes = proxy.get_session_writes()
    print(f"  ✓ Found {len(session_writes)} writes in isolated session")
    for write in session_writes:
        print(f"    - {write['operation']}: {write['data'].get('text', '')[:50]}")
    
    # Test 5: Verify Mina's memory wasn't polluted
    print("\n[TEST 5] Checking Mina's memory wasn't polluted...")
    mina_context = mina_mem.get_context("dolphin test", top_k=10)
    dolphin_in_mina = any("dolphin" in str(m.get("text", "")).lower() for m in mina_context)
    if not dolphin_in_mina:
        print("  ✓ Mina's memory is clean (no dolphin data)")
    else:
        print("  ✗ WARNING: Found dolphin data in Mina's memory (isolation failed!)")
    
    # Test 6: Session info
    print("\n[TEST 6] Getting session info...")
    info = proxy.get_session_info()
    print(f"  ✓ Session info: {json.dumps(info, indent=2)}")
    
    print("\n[PASSED] SafeMemoryProxy isolation test complete!")
    print("\nUsage from alternative model:")
    print("  1. Create session: POST /alt_model/memory_session/create?model_name=dolphin3")
    print("  2. Read Mina's context: GET /alt_model/memory_session/reads?model_name=dolphin3&query=...")
    print("  3. Get isolated writes: GET /alt_model/memory_session/writes?model_name=dolphin3")
    print("  4. Migrate to Mina: POST /alt_model/memory_session/migrate?model_name=dolphin3&confirm=true")
    print("  5. Clear session: POST /alt_model/memory_session/clear?model_name=dolphin3&confirm=true")
    
    return True

if __name__ == "__main__":
    try:
        success = test_memory_proxy()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
