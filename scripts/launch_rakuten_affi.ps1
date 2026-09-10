[CmdletBinding()]
param([switch]$NoBrowser)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$entryPoint = Join-Path $projectRoot "app\main.py"
$appUrl = "http://localhost:8501"
$healthUrl = "$appUrl/_stcore/health"
$dataDirectory = Join-Path $projectRoot "data"
$pidPath = Join-Path $dataDirectory "rakuten_affi_streamlit.pid"
$stdoutPath = Join-Path $dataDirectory "rakuten_affi_streamlit.stdout.log"
$stderrPath = Join-Path $dataDirectory "rakuten_affi_streamlit.stderr.log"
$venvConfigPath = Join-Path $projectRoot ".venv\pyvenv.cfg"
$basePythonPath = ""
if (Test-Path -LiteralPath $venvConfigPath -PathType Leaf) {
    $executableLine = Get-Content -LiteralPath $venvConfigPath -Encoding UTF8 | `
        Where-Object { $_ -match '^executable\s*=\s*' } | Select-Object -First 1
    if ($executableLine) {
        $basePythonPath = ($executableLine -replace '^executable\s*=\s*', '').Trim()
    }
}

function Show-RakutenAffiError {
    param([Parameter(Mandatory = $true)][string]$Message)

    if ($NoBrowser) {
        [Console]::Error.WriteLine($Message)
        return
    }
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        $Message,
        "Rakuten Affi",
        "OK",
        "Error"
    ) | Out-Null
}

function Test-StreamlitHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 2
        return $response.StatusCode -eq 200 -and $response.Content.Trim() -eq "ok"
    }
    catch {
        return $false
    }
}

function Test-RakutenAffiProcess {
    param([object]$ProcessInfo)

    if ($null -eq $ProcessInfo -or [string]::IsNullOrWhiteSpace($ProcessInfo.CommandLine)) {
        return $false
    }
    $commandLine = $ProcessInfo.CommandLine.Replace("\", "/")
    $expectedPython = $pythonPath.Replace("\", "/")
    $expectedBasePython = $basePythonPath.Replace("\", "/")
    $expectedEntryPoint = $entryPoint.Replace("\", "/")
    $pythonMatches = (
        $commandLine.Contains($expectedPython) -or
        (-not [string]::IsNullOrWhiteSpace($expectedBasePython) -and
            $commandLine.Contains($expectedBasePython))
    )
    $entryPointMatches = (
        $commandLine.Contains($expectedEntryPoint) -or
        $commandLine.Contains("streamlit run app/main.py")
    )
    return (
        $pythonMatches -and
        $commandLine.Contains("streamlit") -and
        $entryPointMatches
    )
}

function Get-RakutenAffiProcess {
    if (Test-Path -LiteralPath $pidPath -PathType Leaf) {
        $savedPidText = (Get-Content -LiteralPath $pidPath -Raw).Trim()
        $savedPid = 0
        if ([int]::TryParse($savedPidText, [ref]$savedPid)) {
            $savedProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $savedPid" `
                -ErrorAction SilentlyContinue
            if (Test-RakutenAffiProcess $savedProcess) {
                return $savedProcess
            }
        }
    }

    $listeners = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" `
            -ErrorAction SilentlyContinue
        if (Test-RakutenAffiProcess $candidate) {
            return $candidate
        }
    }
    return $null
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Show-RakutenAffiError "Python environment was not found. Check the project's .venv folder."
    exit 1
}

if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
    Show-RakutenAffiError "The application entry point was not found."
    exit 1
}

New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
$existingProcess = Get-RakutenAffiProcess
$streamlitHealthy = Test-StreamlitHealth
$portListeners = @(Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue)
if ($null -ne $existingProcess) {
    Set-Content -LiteralPath $pidPath -Value $existingProcess.ProcessId -Encoding ASCII
}

if ($portListeners.Count -gt 0 -and $null -eq $existingProcess) {
    Show-RakutenAffiError (
        "Port 8501 is already used by another application. Close it or change the port."
    )
    exit 1
}

if (-not $streamlitHealthy) {
    $streamlitArguments = @(
        "-m",
        "streamlit",
        "run",
        $entryPoint,
        "--server.port=8501",
        "--server.headless=true",
        "--browser.gatherUsageStats=false"
    )
    $startedProcess = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList $streamlitArguments `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru
    Set-Content -LiteralPath $pidPath -Value $startedProcess.Id -Encoding ASCII

    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ((Test-StreamlitHealth) -and (Get-RakutenAffiProcess)) {
            break
        }
    }
}

if ((Test-StreamlitHealth) -and (Get-RakutenAffiProcess)) {
    if (-not $NoBrowser) {
        Start-Process $appUrl
    }
    exit 0
}

Show-RakutenAffiError "The application could not start. Check whether port 8501 is available."
exit 1
