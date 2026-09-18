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


class _FakeMemoryCapture:
    def __init__(self):
        self.captured = None

    def find_memory_id_by_text(self, *args, **kwargs):
        return None

    def add_memory(self, text, *args, **kwargs):
        self.captured = text
        return 555


def test_memory_write_preserves_mid_clause_remember_that():
    # Transcript 2026-09-16: "...you may want to remember that." was truncated to
    # "...you may want to" by a blind endswith("remember that") strip.
    fake = _FakeMemoryCapture()
    with patch('memory.mk1_memory.MK1Memory', return_value=fake):
        out = memory_write.tool_entry({
            'text': 'We live in Spokane Washington you may want to remember that.',
            'kind': 'fact',
            'tags': ['user_memory'],
        })

    assert out['ok'] is True
    assert out['stored'].lower().endswith('remember that')


def test_memory_write_strips_standalone_trailing_remember_that():
    fake = _FakeMemoryCapture()
    with patch('memory.mk1_memory.MK1Memory', return_value=fake):
        out = memory_write.tool_entry({
            'text': 'We live in Spokane Washington, remember that.',
            'kind': 'fact',
            'tags': ['user_memory'],
        })

    assert out['ok'] is True
    assert out['stored'].lower() == 'we live in spokane washington'
