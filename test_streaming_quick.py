#!/usr/bin/env python
import requests
import json

try:
    r = requests.post(
        'http://localhost:8000/process-stream',
        json={'input': 'say hello world'},
        timeout=60,
        stream=True
    )
    print(f'Status: {r.status_code}')
    print('Streaming response:\n')
    
    for line in r.iter_lines():
        if not line:
            continue
        line_str = line.decode('utf-8') if isinstance(line, bytes) else line
        if line_str.startswith('data: '):
            line_str = line_str[6:]
        try:
            chunk = json.loads(line_str)
            if chunk.get('type') == 'chunk':
                print(chunk.get('content', ''), end='', flush=True)
            elif chunk.get('type') == 'done':
                print('\n\n[Stream complete]')
        except json.JSONDecodeError:
            pass
            
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
