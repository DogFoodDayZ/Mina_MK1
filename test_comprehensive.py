import requests
import time
import json

def main() -> None:
    time.sleep(1)

    # Test 1: Different weather phrasing
    r1 = requests.post(
        'http://localhost:8000/process',
        json={'input': 'weather in new york'},
        timeout=20,
    )
    print('Test 1 - "weather in new york":')
    lines = r1.json().get('reply', '').split('\n')
    for line in lines[:3]:
        print(f'  {line}')
    print()

    # Test 2: Code execution
    r2 = requests.post(
        'http://localhost:8000/process',
        json={'input': 'execute python: x = [1,2,3,4,5]; print(sum(x))'},
        timeout=20,
    )
    print('Test 2 - Code execution:')
    print(f'  {r2.json().get("reply", "")[:100]}')
    print()

    # Test 3: Streaming endpoint
    r3 = requests.post(
        'http://localhost:8000/process-stream',
        json={'input': 'say hello world'},
        timeout=30,
        stream=True,
    )
    print('Test 3 - Streaming:')
    chunks = []
    for line in r3.iter_lines():
        if line and line.startswith(b'data: '):
            try:
                data = json.loads(line[6:])
                if data.get('type') == 'chunk':
                    chunks.append(data.get('content', ''))
            except Exception:
                pass
    print(f'  Got {len(chunks)} chunks')
    if chunks:
        content = ''.join(chunks)
        print(f'  Content: {content[:60]}...')


if __name__ == '__main__':
    main()
