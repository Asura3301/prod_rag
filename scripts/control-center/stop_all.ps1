[CmdletBinding()]
param(
    [string]$Config = "ops/control-center/settings.yaml",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

& (Join-Path $PSScriptRoot "_invoke.ps1") -Action stop -All -Config $Config -ExtraArgs $ExtraArgs
exit $LASTEXITCODE

