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
