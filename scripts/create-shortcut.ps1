# Agent Workshop Hub - create a desktop shortcut that starts the app window with the built-in terminal.
[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()][string]$Name = "Agent Workshop",
    [ValidateNotNullOrEmpty()][string]$Distro = "Ubuntu-24.04",
    [switch]$NoTerminal,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $repo "start.ps1"
if (-not (Test-Path -LiteralPath $launcher)) { throw "Missing start.ps1 next to the scripts folder" }
$desktop = [Environment]::GetFolderPath("Desktop")
if (-not $desktop) { $desktop = Join-Path $HOME "Desktop" }
$link = Join-Path $desktop ($Name + ".lnk")
$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`" -Distro $Distro"
if (-not $NoTerminal) { $arguments += " -Terminal" }
if ($DryRun) {
    Write-Host "[dry-run] Would create shortcut: $link"
    Write-Host "[dry-run] Target: powershell.exe $arguments"
    Write-Host "[dry-run] Nothing was created."
    return
}
if (Test-Path -LiteralPath $link) { throw "A shortcut already exists: $link (delete it or choose -Name)" }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe")
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $repo
$shortcut.WindowStyle = 7   # minimized: the console hosts the server; closing it stops the Hub
$shortcut.Description = "Start Agent Workshop Hub (local only)"
$shortcut.Save()
Write-Host "Created: $link"
