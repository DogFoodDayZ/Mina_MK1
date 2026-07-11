#!/usr/bin/env python3
"""Test file write tool directly."""
from tools.file_write import tool_entry

args = {"path": "test_direct.txt", "content": "hello world from direct test"}
result = tool_entry(args)
print("Result:", result)
