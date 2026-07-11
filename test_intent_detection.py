#!/usr/bin/env python3
"""Debug intent detection."""
from agent.core import MK1Core

agent = MK1Core()

tests = [
    "create a folder called test_mina_features",
    "write to file test_mina_features\\hello.txt: Hello from Mina!",
    "read the file test_mina_features\\hello.txt",
    "what system am i on",
    "tell me a joke about debugging",
]

for text in tests:
    tool_name, args = agent.detect_tool_intent(text)
    print(f"Input: {text}")
    print(f"  → Tool: {tool_name}")
    print(f"  → Args: {args}")
    print()
