Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$cmdPath = Join-Path $root 'Start Mina Tray.cmd'
if (-not (Test-Path $cmdPath)) {
    throw "Missing launcher: $cmdPath"
}

$taskName = 'MinaTrayLauncher'
$runRegPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runRegName = 'MinaTrayLauncher'
$runRegValue = "cmd.exe /c `"$cmdPath`""

function Register-MinaStartup {
    try {
        $taskAction = New-ScheduledTaskAction -Execute $cmdPath -WorkingDirectory $root
        $taskTrigger = New-ScheduledTaskTrigger -AtLogOn
        $taskPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
        $taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
        Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $taskTrigger -Principal $taskPrincipal -Settings $taskSettings -Force | Out-Null
        return 'scheduled_task'
    }
    catch {
        New-Item -Path $runRegPath -Force | Out-Null
        Set-ItemProperty -Path $runRegPath -Name $runRegName -Value $runRegValue -Type String -Force
        return 'run_key'
    }
}

$method = Register-MinaStartup

Write-Host "Installed Windows logon startup via $method"
Write-Host 'Mina tray will now auto-start on login.'
