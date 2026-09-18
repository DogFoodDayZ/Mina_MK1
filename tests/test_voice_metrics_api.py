"""Tests for the /voice/metrics endpoints used by the GUI audio tab.

The voice loop POSTs a live VAD snapshot; the GUI polls GET for the latest
reading plus a short history. No live core, no mic, no memory writes.
"""

import sys
import time

from fastapi.testclient import TestClient

sys.path.insert(0, 'e:/Mina_MK1')

import mk1_api


def test_voice_metrics_push_and_latest():
    client = TestClient(mk1_api.app)

    # Reset module state between tests.
    with mk1_api._voice_metrics_lock:
        mk1_api._voice_metrics_latest = {}
        mk1_api._voice_metrics_history.clear()

    snap = {
        "ts": time.time(),
        "rms": 0.012,
        "noise_floor": 0.004,
        "threshold": 0.012,
        "capturing": True,
        "voiced_chunks": 5,
        "muted": False,
        "device": 1,
        "snr_ratio": 3.0,
        "noise_floor_alpha": 0.98,
    }

    r = client.post("/voice/metrics", json=snap)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    g = client.get("/voice/metrics")
    assert g.status_code == 200
    body = g.json()
    assert body["ok"] is True
    assert body["latest"]["rms"] == 0.012
    assert body["latest"]["noise_floor"] == 0.004
    assert body["latest"]["capturing"] is True
    assert body["live"] is True  # just posted, so it's fresh


def test_voice_metrics_history_ring_and_live_flag():
    client = TestClient(mk1_api.app)

    with mk1_api._voice_metrics_lock:
        mk1_api._voice_metrics_latest = {}
        mk1_api._voice_metrics_history.clear()

    # Push several snapshots; history should accumulate and GET should return them.
    for i in range(5):
        client.post("/voice/metrics", json={"ts": time.time(), "rms": 0.001 * (i + 1), "noise_floor": 0.001, "threshold": 0.003, "capturing": False})

    g = client.get("/voice/metrics?history=10")
    body = g.json()
    assert len(body["history"]) == 5
    assert body["history"][-1]["rms"] == 0.005

    # A stale snapshot (old ts) should report live=False.
    with mk1_api._voice_metrics_lock:
        mk1_api._voice_metrics_latest = {"ts": time.time() - 30.0, "rms": 0.001}
    g2 = client.get("/voice/metrics")
    assert g2.json()["live"] is False
