#!/usr/bin/env python3
"""Debug file write intent."""
from agent.core import MK1Core

agent = MK1Core()

text = "write to file test.txt: hello world"
tool_name, args = agent.detect_tool_intent(text)
print(f"Tool: {tool_name}")
print(f"Args: {args}")
print(f"Content extracted: '{args.get('content', '')}'")
print(f"Content is empty: {not args.get('content', '').strip()}")
