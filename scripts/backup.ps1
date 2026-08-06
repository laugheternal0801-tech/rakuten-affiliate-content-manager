$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$databasePath = Join-Path $projectRoot "data\app.db"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDirectory = Join-Path $projectRoot "backups\$timestamp"

if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) {
    throw "バックアップ対象がありません: $databasePath"
}

New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
Copy-Item -LiteralPath $databasePath -Destination (Join-Path $backupDirectory "app.db")
Write-Output "バックアップを作成しました: $backupDirectory"
