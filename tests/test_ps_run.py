import sys
sys.path.insert(0, 'e:/Mina_MK1')

from agent.core import MK1Core
from tools import ps_run
from tools.powershell import tool_entry as powershell_tool_entry
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


def test_ps_run_rewrites_invalid_physical_memory_command():
    result = tool_entry({'script': 'Get-PhysicalMemory | Select-Object TotalVisibleMemoryInMB'})
    assert result['ok'] is True
    assert result['result']['stdout']
    assert 'TotalVisibleMemory' in result['result']['stdout'] or 'FreePhysicalMemory' in result['result']['stdout']


def test_powershell_tool_runs_multiple_commands_in_one_call():
    result = powershell_tool_entry({
        'commands': [
            'Write-Output "alpha"',
            'Write-Output "beta"',
        ],
    })
    assert result['ok'] is True
    assert 'alpha' in result['result']['stdout']
    assert 'beta' in result['result']['stdout']


def test_core_detects_system_queries_for_ps_run():
    core = MK1Core()
    name, args = core.detect_tool_intent('what system am i running on?')
    assert name == 'ps_run'
    assert 'Get-ComputerInfo' in args['script']


def test_ps_run_rejects_unapproved_directory_paths():
    result = tool_entry({'script': 'Get-ChildItem C:\\Windows', 'cwd': 'D:\\tmp'})
    assert result['ok'] is False
    assert 'approved' in result['error'].lower()


def test_core_formats_failed_powershell_with_stderr():
    core = MK1Core()
    formatted = core._format_tool_result(
        'ps_run',
        {
            'ok': False,
            'error': "Get-Processor : The term 'Get-Processor' is not recognized",
            'result': {
                'stdout': '',
                'stderr': "Get-Processor : The term 'Get-Processor' is not recognized",
                'exit_code': 1,
            },
        },
    )
    assert 'STDERR:' in formatted
    assert 'Get-Processor' in formatted


def test_core_formats_generic_failed_tool_with_stderr():
    core = MK1Core()
    formatted = core._format_tool_result(
        'mystery_tool',
        {
            'ok': False,
            'error': 'command rejected',
            'result': {
                'stdout': '',
                'stderr': 'permission denied',
                'status': 'denied',
            },
        },
    )
    assert 'mystery_tool failed' in formatted
    assert 'STDERR:' in formatted
    assert 'permission denied' in formatted


def test_normalize_command_maps_natural_cpu_question():
    cmd = ps_run._normalize_command("what cpu are you on?")
    assert "Win32_Processor" in cmd


def test_normalize_command_maps_list_gpu_prompt_with_apostrophe():
    cmd = ps_run._normalize_command("list gpu's")
    assert "Win32_VideoController" in cmd
    assert "AdapterRAM" in cmd


def test_normalize_command_rewrites_invalid_get_processor_variant():
    cmd = ps_run._normalize_command("Get-Processor")
    assert "Win32_Processor" in cmd


def test_normalize_command_rewrites_probe_cpu_request_with_filler_words():
    cmd = ps_run._normalize_command("yes lets probe the CPU and see what comes up.")
    assert "Win32_Processor" in cmd


def test_normalize_command_maps_natural_time_question():
    cmd = ps_run._normalize_command("what time is it?")
    assert "Get-Date" in cmd


def test_normalize_command_maps_natural_date_question():
    cmd = ps_run._normalize_command("what date is it?")
    assert "Get-Date" in cmd
