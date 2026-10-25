# Register only after reviewing on the collection computer. No SYSTEM account.
param(
  [Parameter(Mandatory=$true)][string]$Python,
  [Parameter(Mandatory=$true)][string]$EnvironmentFile,
  [switch]$Install
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$PythonPath = (Resolve-Path $Python).Path
$ConfigPath = (Resolve-Path $EnvironmentFile).Path
$Action = New-ScheduledTaskAction -Execute $PythonPath -Argument "-m api.serve --env-file `"$ConfigPath`"" -WorkingDirectory $ProjectRoot
$User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$Settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$Task = New-ScheduledTask -Action $Action -Principal $Principal -Trigger $Trigger -Settings $Settings
if ($Install) {
  if (Get-ScheduledTask -TaskName 'MediaCrawlerWorkbench' -ErrorAction SilentlyContinue) { throw 'Task already exists; preserve and review its configuration before updating.' }
  Register-ScheduledTask -TaskName 'MediaCrawlerWorkbench' -InputObject $Task
} else {
  $Task | Export-ScheduledTask
  Write-Host 'Preview only. Add -Install to register for the current interactive user.'
}
