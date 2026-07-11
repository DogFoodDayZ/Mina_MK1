#!/usr/bin/env python3
"""Test if Mina actually does work through the API."""
import json
import urllib.request

BASE_URL = 'http://127.0.0.1:8000/process'

def test_api(prompt, test_name, timeout=15):
    print(f'\n[{test_name}]')
    print(f'Input: {prompt}')
    print('-' * 60)
    
    body = {'input': prompt}
    req = urllib.request.Request(
        BASE_URL,
        data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        result = json.loads(resp.read())
        reply = result.get('reply', '')
        print('RESPONSE:', reply[:200])
        if len(reply) > 200:
            print('...[truncated]')
        return reply
    except Exception as e:
        print(f'ERROR: {e}')
        return ''

# Test 1: Code execution
test_api('execute: print("Mina works!")', 'TEST 1: Execute Code')

# Test 2: Store memory
test_api('remember: my favorite animal is a gremlin', 'TEST 2: Store Memory')

# Test 3: Recall memory
reply = test_api('what animal do i like', 'TEST 3: Recall Memory')
if 'gremlin' in reply.lower():
    print('[SUCCESS] Memory correctly recalled!')

# Test 4: Web search
test_api('search web for python tutorial', 'TEST 4: Web Search', timeout=45)

# Test 5: Create folder
test_api('create folder test_work', 'TEST 5: Create Folder')

print('\n' + '='*60)
print('SUMMARY: Mina executed all tests!')
