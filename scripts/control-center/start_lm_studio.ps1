[CmdletBinding()]
param(
    [string]$Executable = $env:LM_STUDIO_EXE
)

$ErrorActionPreference = "Stop"

if (Get-Process -Name "LM Studio" -ErrorAction SilentlyContinue) {
    Write-Output "LM Studio Desktop is already running."
    exit 0
}

$candidates = @()
if ($Executable) {
    $candidates += $Executable
}
if ($env:LOCALAPPDATA) {
    $candidates += (Join-Path $env:LOCALAPPDATA "Programs\LM Studio\LM Studio.exe")
}
if ($env:ProgramFiles) {
    $candidates += (Join-Path $env:ProgramFiles "LM Studio\LM Studio.exe")
}

$application = $candidates |
    Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
    Select-Object -First 1

if (-not $application) {
    Write-Error "LM Studio Desktop was not found. Install it or set LM_STUDIO_EXE."
    exit 1
}

# A visible window is intentional: this script's only job is to open the desktop app.
Start-Process -FilePath $application
Write-Output "LM Studio Desktop launched."
