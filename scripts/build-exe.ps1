# JetAI - build JetAI.exe (double-click launcher) next to jetai.ps1.
# Uses the C# compiler that ships with Windows (.NET Framework 4); nothing is downloaded or installed.
# The exe only starts jetai.ps1 from its own folder, so keep it in the repository / package folder.
[CmdletBinding()]
param(
    [string]$OutDir = "",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
if (-not $OutDir) { $OutDir = Split-Path -Parent $PSScriptRoot }          # a $PSScriptRoot default inside param() is empty on some PowerShell 5.1 hosts
$source = Join-Path $PSScriptRoot "JetAI.cs"
$icon = Join-Path (Split-Path -Parent $PSScriptRoot) "workshop/static/jetai.ico"
$csc = Get-ChildItem (Join-Path $env:WINDIR "Microsoft.NET\Framework64") -Recurse -Filter csc.exe -ErrorAction SilentlyContinue |
    Sort-Object FullName | Select-Object -Last 1
if (-not $csc) { throw "The C# compiler (csc.exe) was not found under $env:WINDIR\Microsoft.NET\Framework64. Windows 10/11 normally includes it." }
$target = Join-Path $OutDir "JetAI.exe"
$arguments = @("/nologo", "/target:winexe", "/optimize+", "/out:$target", "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll")
if (Test-Path -LiteralPath $icon) { $arguments += "/win32icon:$icon" }
$arguments += $source
if ($DryRun) {
    Write-Host "[dry-run] Compiler: $($csc.FullName)"
    Write-Host "[dry-run] Would build: $target"
    Write-Host "[dry-run] Nothing was created."
    return
}
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
& $csc.FullName @arguments
if ($LASTEXITCODE -ne 0) { throw "Compilation failed (exit code $LASTEXITCODE)." }
Write-Host "Built: $target ($([math]::Round((Get-Item $target).Length / 1KB)) KB). Double-click it to start JetAI."
