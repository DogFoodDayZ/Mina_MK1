from tools.web_search import tool_entry

# Test weather query through tool
result = tool_entry({
    'query': 'whats the weather like in spokane WA',
    'max_results': 5,
    'timeout': 15,
    'include_content': False
})

print(f"OK: {result.get('ok')}")
print(f"Error: {result.get('error')}")
result_data = result.get('result', {})
print(f"Count: {result_data.get('count')}")
print(f"Source: {result_data.get('source')}")

results = result_data.get('results', [])
print(f"\nResults ({len(results)}):")
for r in results:
    print(f"  - {r.get('title', '')}")
    print(f"    {r.get('snippet', '')[:100]}...")
