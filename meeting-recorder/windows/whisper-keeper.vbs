' whisper-keeper.vbs -- launches whisper-keeper.ps1 with no console window, ever.
'
' Why this exists: running powershell.exe directly from Task Scheduler creates a
' console window first and only then applies -WindowStyle Hidden, so a black box
' flashes on screen at every run. This task repeats every 3 minutes, so that is a
' flash every 3 minutes for as long as the machine is on.
' WScript.Shell.Run applies window style 0 at process-creation time, so no window
' is ever created.
'
' The third argument (True) makes this wait for the keeper to finish, so Task
' Scheduler's MultipleInstances=IgnoreNew works and LastTaskResult stays useful.
'
' Deploy to C:\tools\whisper-keeper.vbs next to whisper-keeper.ps1;
' install-whisper-service.ps1 points the scheduled task at this file.

Dim sh, cmd, rc
Set sh = CreateObject("WScript.Shell")

cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\tools\whisper-keeper.ps1"""

rc = sh.Run(cmd, 0, True)

' Surface the keeper's exit code as the task's LastTaskResult.
WScript.Quit rc
