from unittest.mock import patch

from tools import memory_write


class _FakeMemoryNone:
    def find_memory_id_by_text(self, *args, **kwargs):
        return None

    def add_memory(self, *args, **kwargs):
        return None


class _FakeMemoryOK:
    def find_memory_id_by_text(self, *args, **kwargs):
        return None

    def add_memory(self, *args, **kwargs):
        return 123


class _FakeMemoryDuplicate:
    def __init__(self):
        self.add_called = False

    def find_memory_id_by_text(self, *args, **kwargs):
        return 777

    def add_memory(self, *args, **kwargs):
        self.add_called = True
        return 999


def test_memory_write_returns_error_when_add_memory_returns_none():
    with patch('memory.mk1_memory.MK1Memory', return_value=_FakeMemoryNone()):
        out = memory_write.tool_entry({'text': 'my eyes are blue', 'kind': 'fact', 'tags': ['user_memory']})

    assert out['ok'] is False
    assert 'memory_write_failed' in out['error']


def test_memory_write_success_when_add_memory_returns_id():
    with patch('memory.mk1_memory.MK1Memory', return_value=_FakeMemoryOK()):
        out = memory_write.tool_entry({'text': 'my eyes are blue', 'kind': 'fact', 'tags': ['user_memory']})

    assert out['ok'] is True
    assert out['id'] == 123
    assert out['stored'] == 'my eyes are blue'


def test_memory_write_deduplicates_existing_user_memory_fact():
    fake = _FakeMemoryDuplicate()

    with patch('memory.mk1_memory.MK1Memory', return_value=fake):
        out = memory_write.tool_entry({'text': 'my eyes are blue', 'kind': 'fact', 'tags': ['user_memory']})

    assert out['ok'] is True
    assert out['id'] == 777
    assert out['stored'] == 'my eyes are blue'
    assert out['deduplicated'] is True
    assert fake.add_called is False
