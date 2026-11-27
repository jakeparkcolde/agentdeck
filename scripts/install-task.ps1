<#
agentdeck 대시보드를 Windows 로그온 때 자동으로 실행한다 (작업 스케줄러). macOS 의 install-launchd.sh 에 해당.

  .\scripts\install-task.ps1              # 설치: .venv 준비 + 작업 등록 (지금 바로 시작하지는 않음)
  .\scripts\install-task.ps1 -Start       # 설치 후 바로 시작
  .\scripts\install-task.ps1 -Uninstall   # 작업 제거 (.venv 는 남김)

- 로그온 작업(사용자 세션)으로 등록한다. Windows 서비스는 화면 없는 세션 0 에서 돌아 USB 장치를 열 수 없다.
- 창 없이(pythonw) 실행하고, 출력은 ~/.cache/d200/dashboard.log 로 간다.
- 프로그램이 죽으면 1분 뒤 다시 실행한다 (케이블을 뽑는 건 프로그램이 알아서 기다리므로 재시작이 필요 없다).
- 직접 실행 중인 대시보드가 있으면 먼저 끄세요. 둘이 같은 장치를 쓰면 화면이 섞인다.
#>
param([switch]$Uninstall, [switch]$Start)
$ErrorActionPreference = 'Stop'
$TaskName = 'agentdeck-dashboard'
$Root = Split-Path -Parent $PSScriptRoot
$User = "$env:USERDOMAIN\$env:USERNAME"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "removed task: $TaskName"
    return
}

# 자동 실행용 고정 환경 (.venv). uv 가 있으면 uv 로, 없으면 py 런처로 만든다
$Venv = Join-Path $Root '.venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
$Pyw = Join-Path $Venv 'Scripts\pythonw.exe'
$Req = Join-Path $Root 'requirements.txt'
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not (Test-Path $Py)) {
    if ($uv) { & $uv.Source venv $Venv --python 3.12 } else { & py -3 -m venv $Venv }
    if (-not (Test-Path $Py)) { throw "could not create $Venv" }
}
if ($uv) { & $uv.Source pip install --python $Py -r $Req } else { & $Py -m pip install -r $Req }
if (-not (Test-Path $Pyw)) { throw "pythonw.exe not found in $Venv" }

$Action = New-ScheduledTaskAction -Execute $Pyw -Argument "-X utf8 `"$Root\scripts\run_windows.py`"" -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal `
    -Description 'agentdeck dashboard (Ulanzi D200/D200H) - starts at logon' -Force | Out-Null
Write-Output "registered task: $TaskName (runs at logon as $User)"
Write-Output "log: $env:USERPROFILE\.cache\d200\dashboard.log"

if ($Start) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Output "started"
}
