param(
  [Parameter(Mandatory = $true)][string]$Username,
  [Parameter(Mandatory = $true)][string]$Password
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Config = Join-Path $Root "atr\acps-cli.toml"
$AcsFile = Join-Path $Root "atr\acs.json"
$PrivateDir = Join-Path $Root "atr\private"
$EabFile = Join-Path $PrivateDir "eab.json"
$CliProjectDir = Join-Path (Split-Path -Parent (Split-Path -Parent $Root)) "ACPs-community\acps-cli"

New-Item -ItemType Directory -Force -Path $PrivateDir | Out-Null

Push-Location $CliProjectDir
try {
  Write-Host "[1/6] 登录 Registry"
  python -m acps_cli.main --config $Config auth login --username $Username --password $Password --json

  Write-Host "[2/6] 保存 ACS 草稿"
  $saveJson = python -m acps_cli.main --config $Config agent save --acs-file $AcsFile --json
  $saveObj = $saveJson | ConvertFrom-Json
  $agentId = $saveObj.id

  if (-not $agentId) {
    throw "未从 agent save 结果中解析出 agent id，请检查输出。"
  }

  Write-Host "[3/6] 提交审核"
  python -m acps_cli.main --config $Config agent submit --agent-id $agentId --json

  Write-Host "[4/6] 检查审核状态（未通过前请反复执行）"
  python -m acps_cli.main --config $Config agent check --acs-file $AcsFile --json

  Write-Host "-----"
  Write-Host "审核通过并且 ACS 已包含 AIC 后，再执行下面两条命令："
  Write-Host "python -m acps_cli.main --config `"$Config`" cert eab fetch --aic <AIC> --output `"$EabFile`" --json"
  Write-Host "python -m acps_cli.main --config `"$Config`" cert issue --aic <AIC> --eab-file `"$EabFile`" --usage serverAuth"
  Write-Host "-----"
}
finally {
  Pop-Location
}
