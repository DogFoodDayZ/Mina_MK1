import sys

from fastapi.testclient import TestClient

sys.path.insert(0, 'e:/Mina_MK1')

import mk1_api


def test_ostranauts_action_queue_lifecycle():
    mk1_api._ostranauts_action_queue.clear()
    client = TestClient(mk1_api.app)

    propose_resp = client.post(
        '/games/ostranauts/actions/propose',
        json={
            'schema_version': 1,
            'source': 'ostranauts_companion',
            'kind': 'ostranauts_action',
            'intent': 'next_action',
            'verb': 'plan',
            'target': 'run',
            'request_text': 'Suggest the safest next action.',
            'plan_text': 'NEXT ACTION: inspect the crew console',
            'telemetry': {'scene': 'CrewSim', 'game_focused': True},
            'requires_confirmation': True,
            'confidence': 0.5,
            'safety': 'review_required',
            'notes': 'drafted from the companion',
        },
    )

    assert propose_resp.status_code == 200
    body = propose_resp.json()
    action_id = body['action']['action_id']

    list_resp = client.get('/games/ostranauts/actions')
    assert list_resp.status_code == 200
    assert list_resp.json()['count'] == 1

    approve_resp = client.post(f'/games/ostranauts/actions/{action_id}/approve')
    assert approve_resp.status_code == 200
    assert approve_resp.json()['action']['approved'] is True
    assert approve_resp.json()['action']['status'] == 'approved'

    discard_resp = client.post(f'/games/ostranauts/actions/{action_id}/discard')
    assert discard_resp.status_code == 200
    assert discard_resp.json()['action']['status'] == 'discarded'

    final_list = client.get('/games/ostranauts/actions')
    assert final_list.status_code == 200
    assert final_list.json()['count'] == 0