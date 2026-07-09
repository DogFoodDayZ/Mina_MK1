import sys
from unittest.mock import patch

sys.path.insert(0, 'e:/Mina_MK1')

from tools.memory_read import tool_entry


class _FakeMemory:
    def __init__(self):
        self.calls = []

    def search(self, query, top_k=1, include_kinds=None, include_tags=None, since_ts=None):
        self.calls.append({
            'query': query,
            'top_k': top_k,
            'include_kinds': include_kinds,
            'include_tags': include_tags,
        })

        if include_kinds == ['fact', 'preference', 'procedure'] and include_tags == ['user_memory']:
            return []

        if include_kinds == ['fact', 'preference', 'procedure'] and include_tags == ['long_term']:
            return []

        if include_kinds == ['fact', 'preference', 'procedure', 'interaction']:
            return [
                {'text': 'what is my birthdate ?', 'kind': 'interaction', 'tags': ['short_term']}
            ]

        if include_kinds is None and include_tags is None:
            return [
                {'text': 'what is my birthdate ?', 'kind': 'interaction', 'tags': ['short_term']}
            ]

        return []

    def recent_memories(self, top_k=100, include_kinds=None, include_tags=None, since_ts=None):
        if include_kinds == ['fact', 'preference', 'procedure'] and include_tags == ['user_memory']:
            return [
                {'text': 'my eyes are deep water blue', 'kind': 'fact', 'tags': ['user_memory']},
                {'text': 'favorite snack is pistachio', 'kind': 'fact', 'tags': ['user_memory']},
            ]
        return []


def test_memory_read_prefers_fact_and_user_memory_first():
    fake = _FakeMemory()

    with patch('memory.mk1_memory.MK1Memory', return_value=fake):
        out = tool_entry({'query': 'what color are my eyes', 'top_k': 3})

    assert out['ok'] is True
    assert out['results'][0]['text'] == 'my eyes are deep water blue'
    assert fake.calls[0]['include_kinds'] == ['fact', 'preference', 'procedure']
    assert fake.calls[0]['include_tags'] == ['user_memory']


def test_memory_read_does_not_match_on_generic_tokens_only():
    fake = _FakeMemory()

    with patch('memory.mk1_memory.MK1Memory', return_value=fake):
        out = tool_entry({'query': 'what is my birthdate?', 'top_k': 3})

    assert out['ok'] is True
    assert out['results'] == []
