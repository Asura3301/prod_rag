[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("start", "stop", "restart", "status", "dashboard")]
    [string]$Action,

    [string]$Config = "ops/control-center/settings.yaml",

    [switch]$All,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "The 'uv' command is required. Install uv and retry from a new terminal."
}

Push-Location $repoRoot
try {
    if ($Action -eq "dashboard") {
        $arguments = @("run", "rag", "dashboard", "--config", $Config)
    }
    else {
        $arguments = @("run", "rag", "services", $Action)
        if ($All) {
            $arguments += "--all"
        }
        $arguments += @("--config", $Config)
    }

    if ($ExtraArgs) {
        $arguments += $ExtraArgs
    }

    & uv @arguments
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode

