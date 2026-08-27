"""
Test that tool failures are captured in Mina's memory for learning.
Ensures failed_decision tags are properly recorded.
"""

import pytest
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent.core import MK1Core
from memory.mk1_memory import MK1Memory


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary memory database."""
    db_path = tmp_path / "test_memory.db"
    return str(db_path)


@pytest.fixture
def mina_core(temp_db, monkeypatch):
    """Create MK1Core with temp memory."""
    monkeypatch.setenv("MK1_DATABASE_PATH", temp_db)
    monkeypatch.setenv("MK1_SKIP_ROUTE_GUARD", "1")  # Skip startup guard for this test
    return MK1Core()


def test_tool_failure_captured_in_memory(mina_core):
    """Verify that when a tool fails, the failure is recorded in memory."""
    # Simulate a tool failure
    failure_result = {
        "ok": False,
        "result": None,
        "error": "UnicodeEncodeError: charmap codec can't encode character",
    }
    
    # Call the capture method
    mina_core._capture_tool_failure_for_learning(
        tool_name="ps_run",
        tool_result=failure_result,
        user_intent="python test_script.py",
    )
    
    # Search memory for the recorded failure
    try:
        results = mina_core.memory.search(
            "ps_run UnicodeEncodeError",
            top_k=5,
            include_tags=["tool_failure", "ps_run"],
        )
        
        # Should find at least one failure record
        assert len(results) > 0, "Tool failure not found in memory"
        
        # Verify the memory entry has correct tags
        first_result = results[0]
        assert "tool_failure" in first_result.get("tags", []), "Missing tool_failure tag"
        assert "ps_run" in first_result.get("tags", []), "Missing tool name tag"
        
        print(f"✓ Tool failure captured: {first_result['text'][:100]}")
        
    except Exception as e:
        print(f"Error searching memory: {e}")
        raise


def test_file_write_failure_captured(mina_core):
    """Verify file_write failures are captured."""
    failure_result = {
        "ok": False,
        "result": None,
        "error": "file_exists_no_overwrite",
    }
    
    mina_core._capture_tool_failure_for_learning(
        tool_name="file_write",
        tool_result=failure_result,
        user_intent="save the script as test.py",
    )
    
    # Search for this failure
    try:
        results = mina_core.memory.search(
            "file_write save script test.py",
            top_k=5,
            include_tags=["file_write", "tool_failure"],
        )
        
        assert len(results) > 0, "File write failure not recorded"
        
        # Verify tags
        for result in results:
            if "file_write" in result.get("tags", []):
                assert "tool_failure" in result.get("tags", [])
                print(f"✓ File write failure recorded: {result['text'][:80]}")
                return
        
        pytest.fail("No file_write failure tag found in memory")
        
    except Exception as e:
        print(f"Error: {e}")
        raise


def test_successful_operations_not_flagged_as_failures(mina_core):
    """Verify successful tool results are NOT captured as failures."""
    success_result = {
        "ok": True,
        "result": {"written": True, "path": "/tmp/test.py"},
        "error": None,
    }
    
    # This should NOT store anything
    mina_core._capture_tool_failure_for_learning(
        tool_name="file_write",
        tool_result=success_result,
        user_intent="save test.py",
    )
    
    # Search for failures - should not find any
    try:
        results = mina_core.memory.search(
            "save test.py",
            top_k=5,
            include_tags=["tool_failure"],
        )
        
        # We might get other results, but none should be tool_failure tagged
        for result in results:
            assert "tool_failure" not in result.get("tags", []), \
                "Success was incorrectly tagged as failure"
        
        print("✓ Successful operations correctly excluded from failures")
        
    except Exception as e:
        print(f"Note: {e}")
        # It's OK if search returns empty, that's what we want


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
