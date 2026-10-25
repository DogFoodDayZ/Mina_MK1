import sys

sys.path.insert(0, 'e:/Mina_MK1')

from memory.mk1_memory import MK1Memory


def test_question_text_not_promotable():
    mem = MK1Memory.__new__(MK1Memory)

    assert mem._is_promotable_text('what color are my eyes') is False
    assert mem._is_promotable_text('what color are my eyes?') is False


def test_fact_text_promotable():
    mem = MK1Memory.__new__(MK1Memory)

    assert mem._is_promotable_text('my eyes are deep water blue') is True


def test_repeated_assistant_reply_is_not_promoted_to_fact():
    # Transcript: Mina twice answered "Biscuit! Three-legged beagle, your little
    # furry tornado." and that reply was stored as a long-term fact.
    reply = "Biscuit! Three-legged beagle, your little furry tornado."
    rows = [
        {"id": 1, "text": reply, "tags": ["short_term", "assistant_turn"]},
        {"id": 2, "text": reply, "tags": ["short_term", "assistant_turn"]},
        {"id": 3, "text": "my dog is a three-legged beagle", "tags": ["short_term", "user_turn"]},
        {"id": 4, "text": "my dog is a three-legged beagle", "tags": ["short_term", "user_turn"]},
    ]
    mem = MK1Memory.__new__(MK1Memory)
    mem.recent_memories = lambda **kwargs: rows
    mem.search = lambda *args, **kwargs: rows
    added = []
    mem._exists_text = lambda text, *args, **kwargs: text in added
    mem.add_memory = lambda text, kind, tags: added.append(text) or len(added)

    mem.auto_promote_short_term("what's my dog called", min_hits=2)

    assert added == ["my dog is a three-legged beagle"]
