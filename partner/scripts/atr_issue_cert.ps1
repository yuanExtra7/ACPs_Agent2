param(
  [Parameter(Mandatory = $true)][string]$Aic
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Config = Join-Path $Root "atr\acps-cli.toml"
$PrivateDir = Join-Path $Root "atr\private"
$EabFile = Join-Path $PrivateDir "eab.json"
$CliProjectDir = Join-Path (Split-Path -Parent (Split-Path -Parent $Root)) "ACPs-community\acps-cli"

New-Item -ItemType Directory -Force -Path $PrivateDir | Out-Null

Push-Location $CliProjectDir
try {
  Write-Host "[1/2] 获取 EAB"
  python -m acps_cli.main --config $Config cert eab fetch --aic $Aic --output $EabFile --json

  Write-Host "[2/2] 申请 serverAuth 证书"
  python -m acps_cli.main --config $Config cert issue --aic $Aic --eab-file $EabFile --usage serverAuth
}
finally {
  Pop-Location
}

