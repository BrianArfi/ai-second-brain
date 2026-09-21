# install-whisper-service.ps1 -- one-time setup so whisper-server stays up for Vexa.
#
# RUN ONCE, ELEVATED (Windows PowerShell as Administrator):
#   powershell -ExecutionPolicy Bypass -File C:\tools\install-whisper-service.ps1
#
# It does three things, all idempotent (safe to re-run):
#   1. Adds a Windows Firewall inbound rule for TCP 8083 (WSL -> Windows host).
#      Without this, packets from the WSL subnet are silently DROPPED and the bot
#      gets an empty transcript even when whisper-server IS running.
#   2. Registers a Scheduled Task that runs whisper-keeper.ps1 at logon and every
#      3 minutes, so a crashed whisper-server self-heals within minutes -- no
#      dependency on WSL PowerShell interop.
#   3. Starts whisper-server right now via the keeper.

$ErrorActionPreference = 'Stop'
$Port     = 8083
$Keeper   = 'C:\tools\whisper-keeper.ps1'
# The task runs the VBS, not the PS1. Task Scheduler calling powershell.exe
# directly creates a console window before -WindowStyle Hidden applies, so a
# black box flashes on screen every 3 minutes. See whisper-keeper.vbs.
$Launcher = 'C:\tools\whisper-keeper.vbs'
$TaskName = 'WhisperServer-Keeper'

function Assert-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p  = New-Object Security.Principal.WindowsPrincipal($id)
  if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'ERROR: run this in an ELEVATED PowerShell (Run as administrator).' -ForegroundColor Red
    exit 1
  }
}
Assert-Admin

if (-not (Test-Path $Keeper)) {
  Write-Host "ERROR: $Keeper not found. Copy whisper-keeper.ps1 to C:\tools first." -ForegroundColor Red
  exit 1
}
if (-not (Test-Path $Launcher)) {
  Write-Host "ERROR: $Launcher not found. Copy whisper-keeper.vbs to C:\tools first." -ForegroundColor Red
  exit 1
}

# 1) Firewall rule ---------------------------------------------------------------
$rule = Get-NetFirewallRule -DisplayName 'Whisper Server 8083 (WSL bots)' -ErrorAction SilentlyContinue
if (-not $rule) {
  New-NetFirewallRule -DisplayName 'Whisper Server 8083 (WSL bots)' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Any | Out-Null
  Write-Host "OK: firewall inbound rule added for TCP $Port" -ForegroundColor Green
} else {
  Write-Host "OK: firewall rule already present" -ForegroundColor Green
}

# 2) Scheduled Task --------------------------------------------------------------
$action  = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "`"$Launcher`""
$atLogon = New-ScheduledTaskTrigger -AtLogOn
# Repeat every 3 minutes. [TimeSpan]::MaxValue serializes to an invalid task-XML
# duration, so use a long-but-valid window (10 years = effectively indefinite).
$repeat  = New-ScheduledTaskTrigger -Once -At (Get-Date) `
  -RepetitionInterval (New-TimeSpan -Minutes 3) -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -MultipleInstances IgnoreNew

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($atLogon, $repeat) `
  -Principal $principal -Settings $settings `
  -Description 'Keeps whisper-server.exe alive on 0.0.0.0:8083 for the Vexa meeting bots.' | Out-Null
Write-Host "OK: scheduled task '$TaskName' registered (at logon + every 3 min)" -ForegroundColor Green

# 3) Start now -------------------------------------------------------------------
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Keeper
Start-Sleep -Seconds 3
$listening = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listening) {
  Write-Host "OK: whisper-server is listening on 0.0.0.0:$Port" -ForegroundColor Green
  Write-Host "Done. Vexa bots will transcribe again." -ForegroundColor Cyan
} else {
  Write-Host "WARN: whisper-server not listening yet. Check C:\tools\whisper-logs\." -ForegroundColor Yellow
}
