[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$BackupDirectory,

    [switch]$ConfirmAppStopped
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-CanonicalPath {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    $resolved = Resolve-Path -LiteralPath $LiteralPath -ErrorAction Stop
    return [System.IO.Path]::GetFullPath($resolved.ProviderPath)
}

function Test-SamePath {
    param(
        [Parameter(Mandatory = $true)][string]$Left,
        [Parameter(Mandatory = $true)][string]$Right
    )

    return [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath($Left),
        [System.IO.Path]::GetFullPath($Right)
    )
}

function Get-ProjectPython {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $virtualEnvironmentPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $virtualEnvironmentPython -PathType Leaf) {
        return Get-CanonicalPath -LiteralPath $virtualEnvironmentPython
    }

    $pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw "Python was not found. Create the project .venv and retry."
    }
    return $pythonCommand.Source
}

if (-not $ConfirmAppStopped) {
    throw "Stop the app and workers, then pass -ConfirmAppStopped."
}

$scriptDirectory = Get-CanonicalPath -LiteralPath $PSScriptRoot
$projectRoot = Get-CanonicalPath -LiteralPath (Join-Path $scriptDirectory "..")
$expectedScriptDirectory = Get-CanonicalPath -LiteralPath (Join-Path $projectRoot "scripts")
if (-not (Test-SamePath -Left $scriptDirectory -Right $expectedScriptDirectory)) {
    throw "This script is not in the expected project scripts directory."
}

$backupRoot = Get-CanonicalPath -LiteralPath (Join-Path $projectRoot "backups")
$candidateBackupDirectory = $BackupDirectory
if (-not [System.IO.Path]::IsPathRooted($candidateBackupDirectory)) {
    $candidateBackupDirectory = Join-Path $projectRoot $candidateBackupDirectory
}
$resolvedBackupDirectory = Get-CanonicalPath -LiteralPath $candidateBackupDirectory
$backupParent = [System.IO.Directory]::GetParent($resolvedBackupDirectory)
if ($null -eq $backupParent -or
    -not (Test-SamePath -Left $backupParent.FullName -Right $backupRoot) -or
    (Test-SamePath -Left $resolvedBackupDirectory -Right $backupRoot)) {
    throw "The restore source must be a direct child of this project's backups directory."
}

$backupDirectoryItem = Get-Item -LiteralPath $resolvedBackupDirectory -Force
if (-not $backupDirectoryItem.PSIsContainer -or
    ($backupDirectoryItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "The restore source must not be a link or reparse point."
}
foreach ($requiredName in @("app.db", "manifest.json", "app.db.sha256")) {
    $requiredPath = Join-Path $resolvedBackupDirectory $requiredName
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "The restore source is missing a required file: $requiredName"
    }
    $requiredItem = Get-Item -LiteralPath $requiredPath -Force
    if (($requiredItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Restore validation files must not be links or reparse points."
    }
}

$expectedDatabasePath = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "data\app.db"))
$expectedDataDirectory = Get-CanonicalPath -LiteralPath (Join-Path $projectRoot "data")
if (-not (Test-SamePath `
    -Left ([System.IO.Path]::GetDirectoryName($expectedDatabasePath)) `
    -Right $expectedDataDirectory)) {
    throw "The restore target is outside the project data directory."
}
if (Test-Path -LiteralPath $expectedDatabasePath) {
    $databasePath = Get-CanonicalPath -LiteralPath $expectedDatabasePath
    if (-not (Test-SamePath -Left $databasePath -Right $expectedDatabasePath)) {
        throw "The restore target is not the project data\app.db."
    }
    $databaseItem = Get-Item -LiteralPath $databasePath -Force
    if ($databaseItem.PSIsContainer -or
        ($databaseItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "The restore target must be a regular database file."
    }
}

$helperPath = Get-CanonicalPath -LiteralPath (Join-Path $scriptDirectory "sqlite_maintenance.py")
$pythonPath = Get-ProjectPython -ProjectRoot $projectRoot
$resultLines = & $pythonPath $helperPath restore `
    --project-root $projectRoot `
    --backup-directory $resolvedBackupDirectory `
    --app-stopped
if ($LASTEXITCODE -ne 0) {
    throw "SQLite restore failed. Any completed pre-restore backup was retained."
}

try {
    $result = ($resultLines -join [System.Environment]::NewLine) | ConvertFrom-Json
} catch {
    throw "The restore result could not be validated."
}
if ([string]::IsNullOrWhiteSpace($result.restored_from) -or
    [string]::IsNullOrWhiteSpace($result.sha256)) {
    throw "The restore result is missing validation metadata."
}

Write-Output "SQLite database restored from: $($result.restored_from)"
if ([string]::IsNullOrWhiteSpace($result.pre_restore_backup)) {
    Write-Output "Pre-restore backup: not needed (the live database was missing)"
} else {
    Write-Output "Pre-restore backup: $($result.pre_restore_backup)"
}
Write-Output "SHA-256: $($result.sha256)"
