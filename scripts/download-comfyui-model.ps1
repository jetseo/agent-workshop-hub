# Download a user-selected ComfyUI checkpoint with mandatory SHA-256 verification.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Url,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$Sha256,
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]*\.(safetensors|ckpt)$')][string]$FileName,
    [string]$Destination = (Join-Path $HOME "ComfyUI"),
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$uri = [Uri]$Url
if ($uri.Scheme -ne "https" -or $uri.UserInfo -or $uri.Host -notin @("huggingface.co","civitai.com","www.civitai.com")) {
    throw "Only HTTPS downloads from huggingface.co or civitai.com are allowed."
}
$root = [System.IO.Path]::GetFullPath($Destination)
$folder = Join-Path $root "models/checkpoints"
$target = Join-Path $folder $FileName
if ($DryRun) {
    Write-Host "[dry-run] Would download $Url to $target and verify SHA-256 $Sha256"
    Write-Host "[dry-run] Existing files are never overwritten."
    return
}
if (-not (Test-Path -LiteralPath (Join-Path $root "main.py"))) {
    throw "ComfyUI checkout not found at $root."
}
if (Test-Path -LiteralPath $target) { throw "Target already exists: $target" }
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$temp = Join-Path $folder ([System.IO.Path]::GetRandomFileName() + ".download")
try {
    Invoke-WebRequest -Uri $uri -OutFile $temp -MaximumRedirection 5 -ErrorAction Stop
    $actual = (Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash
    if ($actual -ne $Sha256) { throw "SHA-256 mismatch. Expected $Sha256; got $actual" }
    if (Test-Path -LiteralPath $target) { throw "Target appeared during download: $target" }
    [System.IO.File]::Move($temp, $target)
    Write-Host "Verified model downloaded: $target"
} finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
}
