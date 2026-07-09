import sys

from fastapi.testclient import TestClient

sys.path.insert(0, 'e:/Mina_MK1')

import mk1_api


class _FakeCore:
    def process(self, user_input):
        if 'save that' in user_input.lower():
            return {'reply': 'Stored memory: my eyes are deep water blue'}
        if 'what color are my eyes' in user_input.lower():
            return {'reply': 'my eyes are deep water blue'}
        return {'reply': 'unknown'}


def test_api_process_save_then_recall(monkeypatch):
    monkeypatch.setattr(mk1_api, 'core', _FakeCore())
    client = TestClient(mk1_api.app)

    save_resp = client.post('/process', json={'input': 'my eyes are deep water blue save that.'})
    read_resp = client.post('/process', json={'input': 'what color are my eyes'})

    assert save_resp.status_code == 200
    assert read_resp.status_code == 200
    assert save_resp.json()['reply'] == 'Stored memory: my eyes are deep water blue'
    assert read_resp.json()['reply'] == 'my eyes are deep water blue'
