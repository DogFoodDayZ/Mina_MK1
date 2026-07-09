import sys
sys.path.insert(0, 'e:/Mina_MK1')

from agent.core import MK1Core
from tools import ps_run
from tools.ps_run import tool_entry


def test_ps_run_executes_basic_command():
    result = tool_entry({'command': 'Write-Output "hello"'})
    assert result['ok'] is True
    assert 'hello' in result['result']['stdout']


def test_ps_run_accepts_script_style_input():
    result = tool_entry({'script': 'Write-Output "hello from script"'})
    assert result['ok'] is True
    assert 'hello from script' in result['result']['stdout']


def test_ps_run_understands_hardware_info_prompt():
    result = tool_entry({'script': 'hardware info'})
    assert result['ok'] is True
    assert result['result']['stdout']


def test_core_detects_system_queries_for_ps_run():
    core = MK1Core()
    name, args = core.detect_tool_intent('what system am i running on?')
    assert name == 'ps_run'
    assert args['script'] == 'what system am i running on?'


def test_ps_run_rejects_unapproved_directory_paths():
    result = tool_entry({'script': 'Get-ChildItem C:\\Windows', 'cwd': 'D:\\tmp'})
    assert result['ok'] is False
    assert 'approved' in result['error'].lower()


def test_normalize_command_maps_natural_cpu_question():
    cmd = ps_run._normalize_command("what cpu are you on?")
    assert "Win32_Processor" in cmd


def test_normalize_command_maps_natural_time_question():
    cmd = ps_run._normalize_command("what time is it?")
    assert "Get-Date" in cmd


def test_normalize_command_maps_natural_date_question():
    cmd = ps_run._normalize_command("what date is it?")
    assert "Get-Date" in cmd
