[CmdletBinding()]
param()

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

$scriptDirectory = Get-CanonicalPath -LiteralPath $PSScriptRoot
$projectRoot = Get-CanonicalPath -LiteralPath (Join-Path $scriptDirectory "..")
$expectedScriptDirectory = Get-CanonicalPath -LiteralPath (Join-Path $projectRoot "scripts")
if (-not (Test-SamePath -Left $scriptDirectory -Right $expectedScriptDirectory)) {
    throw "This script is not in the expected project scripts directory."
}

$databasePath = Join-Path $projectRoot "data\app.db"
if (-not (Test-Path -LiteralPath $databasePath -PathType Leaf)) {
    throw "The backup source data\app.db does not exist."
}
$resolvedDatabasePath = Get-CanonicalPath -LiteralPath $databasePath
if (-not (Test-SamePath -Left $resolvedDatabasePath -Right $databasePath)) {
    throw "The backup source is not the project data\app.db."
}
$databaseItem = Get-Item -LiteralPath $resolvedDatabasePath -Force
if (($databaseItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "The data\app.db file must not be a link or reparse point."
}

$helperPath = Get-CanonicalPath -LiteralPath (Join-Path $scriptDirectory "sqlite_maintenance.py")
$pythonPath = Get-ProjectPython -ProjectRoot $projectRoot
$resultLines = & $pythonPath $helperPath backup --project-root $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "SQLite backup failed. The source database was not changed."
}

try {
    $result = ($resultLines -join [System.Environment]::NewLine) | ConvertFrom-Json
} catch {
    throw "The backup result could not be validated."
}
if ([string]::IsNullOrWhiteSpace($result.backup_directory) -or
    [string]::IsNullOrWhiteSpace($result.sha256)) {
    throw "The backup result is missing validation metadata."
}

Write-Output "Verified SQLite backup created: $($result.backup_directory)"
Write-Output "SHA-256: $($result.sha256)"
