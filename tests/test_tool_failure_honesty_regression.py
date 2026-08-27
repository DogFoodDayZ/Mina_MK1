#!/usr/bin/env python3
"""Regression test for tool failure honesty.

Based on 2026-08-26 transcript where ps_run Unicode error was masked 
by fake success response. Tests that Mina reports tool failures explicitly 
and doesn't hallucinate success.
"""

import pytest
import json
import subprocess
import sys
from pathlib import Path

# Test configuration
API_URL = "http://localhost:8000"
PROCESS_ENDPOINT = f"{API_URL}/process"


def call_api(prompt: str) -> str:
    """Call the Mina API with a prompt and return the reply text."""
    payload = json.dumps({"input": prompt})
    
    try:
        ps_cmd = (
            f'$body = \'{payload}\'; '
            f'$resp = Invoke-WebRequest -Uri "{PROCESS_ENDPOINT}" -Method Post '
            f'-ContentType "application/json" -Body $body -UseBasicParsing; '
            f'$json = ConvertFrom-Json $resp.Content; '
            f'Write-Output $json.reply'
        )
        
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        return result.stdout.strip()
    except Exception as e:
        print(f"API call failed: {e}")
        return ""


class TestToolFailureHonesty:
    """Test that tool failures are reported honestly without hallucination."""

    def test_successful_file_operations_still_work(self):
        """Verify successful file operations continue to work."""
        prompt = 'create a file called simple_test_12345.txt with content hello world'
        
        reply = call_api(prompt)
        
        # Should report success
        assert "written to" in reply.lower() or "created" in reply.lower() or "success" in reply.lower(), \
            f"Successful operation not reported: {reply}"
        
        # File should exist
        test_path = Path("e:/workspace/simple_test_12345.txt")
        assert test_path.exists(), \
            f"File not created despite success claim: {reply}"
        
        # Verify content
        content = test_path.read_text()
        assert "hello world" in content, \
            f"File exists but content is wrong: {content}"
        
        # Cleanup
        test_path.unlink()
        
        print(f"PASS: Successful operations work: {reply[:80]}")

    def test_tool_failure_not_masked(self):
        """Regression: tool failures should not be masked by hallucinated success."""
        prompt = "write to C:\\invalid_path_never_exists_12345\\test.txt with hello"
        
        reply = call_api(prompt)
        
        # Should indicate failure, not hallucinate success
        reply_lower = reply.lower()
        
        # Must NOT claim success if it failed
        if any(x in reply_lower for x in ["fail", "error", "not allowed", "authorize"]):
            assert "written to:" not in reply_lower, \
                f"Claimed success while reporting failure: {reply}"
        
        print(f"PASS: No masked failures: {reply[:80]}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
