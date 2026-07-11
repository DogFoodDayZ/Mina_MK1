#!/usr/bin/env python
from agent.core import MK1Core

core = MK1Core()
tool_name, tool_args = core.detect_tool_intent('whats the weather like in spokane WA')
print(f'Tool: {tool_name}')
print(f'Query: {tool_args.get("query", "")}')
print(f'Args: {tool_args}')
