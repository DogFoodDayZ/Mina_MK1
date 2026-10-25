"""Contract: Mina decides tool use; core must not run tools for her.

Transcript-grounded prompt from the Aug 5 "model decides" contract (TODO.md):
"what cpu are you on?" must not force ps_run.
"""
import json

from agent.core import MK1Core

PROMPT = "what cpu are you on?"
SUGGESTION = (
    "Ooh, hardware gossip! I could peek with this:\n"
    "```powershell\nGet-CimInstance Win32_Processor\n```"
)


class ScriptedModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append([dict(m) for m in messages])
        text = self.replies.pop(0) if self.replies else ""
        return {"choices": [{"message": {"content": text}}]}


def _core(model, reflex_enabled):
    core = MK1Core.__new__(MK1Core)
    core.reflex_enabled = reflex_enabled
    core.reflex_requires_prefix = False
    core.reflex_prefixes = ["/tool", "tool:", "run tool"]
    core.model = model
    core.reflex_calls = []
    core.tool_calls = []
    core.stored = []

    core._correct_contextual_transcript = lambda text: text
    core._advance_turn_clock = lambda: 1
    core._refresh_idle_checkin_state = lambda: None
    core._consume_idle_checkin_prompt = lambda: ""
    core._consume_away_nag_prompt = lambda: ""
    core._register_user_activity = lambda: None
    core._maybe_run_maintenance_tick = lambda: None
    core._build_process_memory_context = lambda text: ("", "", "")
    core._build_process_messages = lambda **kwargs: ([{"role": "system", "content": "persona"}], None)
    core._handle_model_response = lambda messages, reply: reply["choices"][0]["message"]["content"]
    core._tool_registry = lambda: {"ps_run": {}}

    def fake_reflex(messages, reply, user_input, request_context=None):
        core.reflex_calls.append(user_input)
        return {"choices": [{"message": {"content": "[core ran ps_run]"}}]}

    def fake_execute(tool_name, args_str, user_intent):
        core.tool_calls.append((tool_name, json.loads(args_str)))
        return tool_name, {"ok": True}, "[ps_run result] AMD Ryzen 7"

    core._reflex_tools_and_memory = fake_reflex
    core._execute_process_tool_call = fake_execute
    core._store_turn_memory = lambda **kwargs: core.stored.append(kwargs["assistant_text"])
    return core


def test_switch_off_core_runs_no_tool_before_or_after_mina():
    model = ScriptedModel([SUGGESTION])
    core = _core(model, reflex_enabled=False)

    result = MK1Core.process(core, PROMPT)

    assert core.reflex_calls == []
    assert core.tool_calls == []
    assert len(model.calls) == 1
    assert model.calls[0][-1] == {"role": "user", "content": PROMPT}
    assert result["reply"] == SUGGESTION
    assert core.stored == [SUGGESTION]


def test_legacy_switch_on_cannot_hijack_the_conversation_path():
    model = ScriptedModel([SUGGESTION])
    core = _core(model, reflex_enabled=True)

    result = MK1Core.process(core, PROMPT)

    assert core.reflex_calls == []
    assert core.tool_calls == []
    assert result["reply"] == SUGGESTION


def test_mina_own_tool_call_still_runs_and_she_answers_from_result():
    call = '```json\n{"tool": "ps_run", "args": {"command": "Get-CimInstance Win32_Processor"}}\n```'
    model = ScriptedModel([call, "You're running on an AMD Ryzen 7, gremlin approved."])
    core = _core(model, reflex_enabled=False)

    result = MK1Core.process(core, PROMPT)

    assert core.reflex_calls == []
    assert core.tool_calls == [("ps_run", {"command": "Get-CimInstance Win32_Processor"})]
    assert {"role": "tool", "content": "[ps_run result] AMD Ryzen 7"} in model.calls[1]
    assert result["reply"] == "You're running on an AMD Ryzen 7, gremlin approved."


OVERRIDE_CASES = (
    (
        "What CPU and GPU are actually installed in this computer?",
        "```powershell\nGet-CimInstance Win32_Processor | Select-Object Name\n```",
    ),
    (
        "TRAVIS hay mina how's the memory ?",
        "|tool_call><tool_call|>\n\nHere we go!\n\n|tool_call></tool_call|",
    ),
)


def _rescue_core(reflex_enabled):
    from test_transcript_regression_prompts import _core_for_intent_tests

    core = _core_for_intent_tests()
    core.reflex_enabled = reflex_enabled
    core.reflex_calls = []

    def fake_reflex(messages, reply, user_input, request_context=None):
        core.reflex_calls.append(user_input)
        return {"choices": [{"message": {"content": "[core ran a tool]"}}]}

    core._reflex_tools_and_memory = fake_reflex
    return core


def _rescue(core, user_input, reply_text):
    return MK1Core._maybe_rescue_chat_suggested_action(
        core,
        messages=[],
        user_input=user_input,
        assistant_text=reply_text,
        request_context=None,
    )


def test_after_reply_override_still_fires_when_switch_on_so_gate_is_proven():
    for user_input, reply_text in OVERRIDE_CASES:
        core = _rescue_core(reflex_enabled=True)
        assert _rescue(core, user_input, reply_text) is not None
        assert core.reflex_calls


def test_after_reply_override_is_off_for_all_callers_when_switch_off():
    for user_input, reply_text in OVERRIDE_CASES:
        core = _rescue_core(reflex_enabled=False)
        assert _rescue(core, user_input, reply_text) is None
        assert core.reflex_calls == []
