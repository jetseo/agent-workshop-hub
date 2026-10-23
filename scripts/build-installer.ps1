# JetAI - build JetAI-Setup.exe (installer + uninstaller). Needs the portable package zip from build-package.ps1
# (which downloads the official embeddable Python once over HTTPS from python.org, about 10 MB) and the C# compiler
# that ships with Windows. Output: dist\JetAI-Setup.exe
[CmdletBinding()]
param(
    [string]$OutDir = "",
    [switch]$SkipPackage,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
if (-not $OutDir) { $OutDir = Join-Path $PSScriptRoot "../dist" }          # a $PSScriptRoot default inside param() is empty on some PowerShell 5.1 hosts
$repo = Split-Path -Parent $PSScriptRoot
$zip = Join-Path $OutDir "JetAI-portable.zip"
$csc = Get-ChildItem (Join-Path $env:WINDIR "Microsoft.NET\Framework64") -Recurse -Filter csc.exe -ErrorAction SilentlyContinue |
    Sort-Object FullName | Select-Object -Last 1
if (-not $csc) { throw "csc.exe was not found under $env:WINDIR\Microsoft.NET\Framework64." }
if ($DryRun) {
    Write-Host "[dry-run] Would build the package zip (unless -SkipPackage): $zip"
    Write-Host "[dry-run] Would compile scripts/Setup.cs with $($csc.FullName) embedding the zip as 'payload.zip' -> $OutDir\JetAI-Setup.exe"
    Write-Host "[dry-run] Nothing was downloaded or created."
    return
}
if (-not $SkipPackage) { & (Join-Path $PSScriptRoot "build-package.ps1") -OutDir $OutDir }
if (-not (Test-Path -LiteralPath $zip)) { throw "Missing $zip - run build-package.ps1 first or drop -SkipPackage." }
$target = Join-Path (Resolve-Path $OutDir).Path "JetAI-Setup.exe"
$icon = Join-Path $repo "workshop/static/jetai.ico"
$arguments = @("/nologo", "/target:winexe", "/optimize+", "/out:$target", "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll",
               "/r:System.IO.Compression.dll", "/r:System.IO.Compression.FileSystem.dll", "/resource:$((Resolve-Path $zip).Path),payload.zip")
if (Test-Path -LiteralPath $icon) { $arguments += "/win32icon:$icon" }
$arguments += (Join-Path $PSScriptRoot "Setup.cs")
& $csc.FullName @arguments
if ($LASTEXITCODE -ne 0) { throw "Compilation failed (exit code $LASTEXITCODE)." }
Write-Host "Built: $target ($([math]::Round((Get-Item $target).Length / 1MB, 1)) MB)"
