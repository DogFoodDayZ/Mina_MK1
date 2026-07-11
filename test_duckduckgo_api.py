from tools.web_search import tool_entry
import json

# Test AI agents query
result = tool_entry({'query': 'latest ai news', 'max_results': 5, 'timeout': 15, 'include_content': False})
print('OK:', result.get('ok'))
print('Error:', result.get('error'))
print('Count:', (result.get('result') or {}).get('count'))

results = (result.get('result') or {}).get('results', [])
print(f'\nResults ({len(results)}):')
for r in results[:3]:
    print(f"  - {r.get('title', '')[:60]}")
    print(f"    {r.get('snippet', '')[:80]}")
