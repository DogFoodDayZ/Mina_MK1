#!/usr/bin/env python3
"""Debug specific file write."""
from agent.core import MK1Core

agent = MK1Core()

# Exact test input
text = "write to file test_mina_features\\hello.txt: Hello from Mina! This is a test file created on 2026-07-10 20:31:25"
tool_name, args = agent.detect_tool_intent(text)
print(f"Tool: {tool_name}")
print(f"Path: {args.get('path', '')}")
print(f"Content: '{args.get('content', '')}'")
print(f"Content length: {len(args.get('content', ''))}")
print(f"Content empty: {not args.get('content', '').strip()}")
