[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "Medium")]
param()

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$entryPoint = Join-Path $projectRoot "app\main.py"
$pidPath = Join-Path $projectRoot "data\rakuten_affi_streamlit.pid"
$venvConfigPath = Join-Path $projectRoot ".venv\pyvenv.cfg"
$basePythonPath = ""
if (Test-Path -LiteralPath $venvConfigPath -PathType Leaf) {
    $executableLine = Get-Content -LiteralPath $venvConfigPath -Encoding UTF8 | `
        Where-Object { $_ -match '^executable\s*=\s*' } | Select-Object -First 1
    if ($executableLine) {
        $basePythonPath = ($executableLine -replace '^executable\s*=\s*', '').Trim()
    }
}

if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
    Write-Output "Rakuten Affi PID file was not found. The app may already be stopped."
    exit 0
}

$savedPidText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
$savedPid = 0
if (-not [int]::TryParse($savedPidText, [ref]$savedPid)) {
    throw "Rakuten Affi PID file is invalid. No process was stopped."
}

$processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $savedPid" `
    -ErrorAction SilentlyContinue
if ($null -eq $processInfo) {
    Remove-Item -LiteralPath $pidPath -Force
    Write-Output "Rakuten Affi is already stopped."
    exit 0
}

$commandLine = [string]$processInfo.CommandLine
$normalizedCommand = $commandLine.Replace("\", "/")
$normalizedPython = $pythonPath.Replace("\", "/")
$normalizedBasePython = $basePythonPath.Replace("\", "/")
$normalizedEntryPoint = $entryPoint.Replace("\", "/")
$pythonMatches = (
    $normalizedCommand.Contains($normalizedPython) -or
    (-not [string]::IsNullOrWhiteSpace($normalizedBasePython) -and
        $normalizedCommand.Contains($normalizedBasePython))
)
$entryPointMatches = (
    $normalizedCommand.Contains($normalizedEntryPoint) -or
    $normalizedCommand.Contains("streamlit run app/main.py")
)
$isExpectedProcess = (
    $pythonMatches -and
    $normalizedCommand.Contains("streamlit") -and
    $entryPointMatches
)
if (-not $isExpectedProcess) {
    throw "PID belongs to another process. No process was stopped."
}

if ($PSCmdlet.ShouldProcess("Rakuten Affi process PID $savedPid", "Stop")) {
    Stop-Process -Id $savedPid
    Wait-Process -Id $savedPid -Timeout 10 -ErrorAction SilentlyContinue
    if (Get-Process -Id $savedPid -ErrorAction SilentlyContinue) {
        throw "Rakuten Affi did not stop within 10 seconds; the PID file was retained."
    }
    Remove-Item -LiteralPath $pidPath -Force
    Write-Output "Rakuten Affi was stopped safely."
}
