import agent.server.mk1_api as mk1_api


def test_api_import_uses_no_live_core_during_pytest_collection():
    assert mk1_api.core is None