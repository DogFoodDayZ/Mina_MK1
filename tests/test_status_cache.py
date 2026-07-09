import sys

from fastapi.testclient import TestClient

sys.path.insert(0, 'e:/Mina_MK1')

import mk1_api


class _CacheCore:
    def __init__(self):
        self.core_status_calls = 0
        self.db_status_calls = 0

    def get_core_status(self):
        self.core_status_calls += 1
        return {
            'core_status_calls': self.core_status_calls,
        }

    def get_db_status(self):
        self.db_status_calls += 1
        return {
            'db_status_calls': self.db_status_calls,
        }



def test_status_endpoint_uses_cache(monkeypatch):
    fake = _CacheCore()
    monkeypatch.setattr(mk1_api, 'core', fake)

    mk1_api._status_cache['value'] = None
    mk1_api._status_cache['expires_at'] = 0.0
    mk1_api.STATUS_CACHE_TTL = 60.0

    client = TestClient(mk1_api.app)

    r1 = client.get('/status')
    r2 = client.get('/status')

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()['core_status_calls'] == 1
    assert r2.json()['core_status_calls'] == 1
    assert fake.core_status_calls == 1



def test_db_status_force_refresh_bypasses_cache(monkeypatch):
    fake = _CacheCore()
    monkeypatch.setattr(mk1_api, 'core', fake)

    mk1_api._db_status_cache['value'] = None
    mk1_api._db_status_cache['expires_at'] = 0.0
    mk1_api.DB_STATUS_CACHE_TTL = 60.0

    client = TestClient(mk1_api.app)

    r1 = client.get('/db/status')
    r2 = client.get('/db/status?force_refresh=true')

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()['db_status_calls'] == 1
    assert r2.json()['db_status_calls'] == 2
    assert fake.db_status_calls == 2
