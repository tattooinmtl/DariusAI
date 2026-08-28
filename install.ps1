# DariusAI installer — the one canonical install/update path.
#
# Public one-liner (fresh machine, no clone required):
#
#   irm https://raw.githubusercontent.com/tattooinmtl/DariusAI/main/install.ps1 | iex
#
# Downloads the branch as a zip from GitHub, syncs files into place with
# robocopy (never touches .env, .venv, or the brain), then builds the venv
# and imports skills. No `git clone` — that path fails on a non-empty
# destination folder and produced the "install silently closed my PowerShell
# window with only .venv left behind" bug. Zip mode has no such requirement.
#
# From a checkout, run it directly to update:
#
#   .\install.ps1              # sync latest files in, keep user data
#   .\install.ps1 -Force       # resync even if version already matches
#
# Modes:
#   auto (default)   pick zip. .git in the tree does NOT auto-switch modes
#                    because the whole point is to work without git.
#   zip              download the branch archive from GitHub, robocopy files
#
# Version comes from version_lock.json on the branch — same file the website
# badge fetches. Push a bump, both the app and the site update from one source.
#
# Safe to re-run. It never overwrites .env, .venv, the brain, or any local
# state: no secret is ever downloaded, generated or required — only
# .env.example is put in place, and only when .env is absent.

[CmdletBinding()]
param(
    [string]$Dest,
    [string]$RepoOwner = 'tattooinmtl',
    [string]$RepoName = 'DariusAI',
    [string]$Branch = 'main',
    [ValidateSet('auto', 'zip')]
    [string]$Mode = 'auto',
    [switch]$Force,
    [switch]$SkipShortcuts
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$MinPython = @(3, 11)

function Info([string]$Message) { Write-Host "[DariusAI Installer] $Message" }
function Warn([string]$Message) { Write-Host "[DariusAI Installer] WARN: $Message" -ForegroundColor Yellow }

# When install.ps1 is fed to `irm | iex`, `exit 1` on failure kills the whole
# PowerShell window instantly and no error text stays visible. Fail() writes
# to a persistent log AND holds the window open so the message can be read.
function Fail([string]$Message) {
    Write-Host ''
    Write-Host "[DariusAI Installer] ERROR: $Message" -ForegroundColor Red
    try {
        $logDir = if ($script:ResolvedDest -and (Test-Path (Split-Path -Parent $script:ResolvedDest))) {
            Split-Path -Parent $script:ResolvedDest
        } else {
            $HOME
        }
        $logPath = Join-Path $logDir 'dariusai-install-error.log'
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        "[$stamp] $Message`r`n" | Out-File -FilePath $logPath -Append -Encoding utf8
        Write-Host "  (also saved to $logPath)" -ForegroundColor DarkGray
    } catch {}
    if ($Host.Name -eq 'ConsoleHost') {
        Write-Host ''
        Write-Host 'Press Enter to close.' -ForegroundColor DarkGray
        try { [void][System.Console]::ReadLine() } catch {}
    }
    throw $Message
}

function Ensure-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { Fail "$Name not found. $Hint" }
}

# Where DariusAI lives. Explicit -Dest wins. Otherwise: running from an
# existing checkout targets that checkout; piped from the web targets the
# default user install dir.
function Resolve-InstallDir() {
    if ($Dest) {
        return (New-Item -ItemType Directory -Path $Dest -Force).FullName
    }
    if ($PSScriptRoot) {
        $candidate = $PSScriptRoot
        if (Test-Path (Join-Path $candidate 'pyproject.toml')) { return $candidate }
    }
    return (New-Item -ItemType Directory -Path (Join-Path $HOME 'dariusai-harness') -Force).FullName
}

function Get-LatestVersion() {
    $url = "https://raw.githubusercontent.com/$RepoOwner/$RepoName/$Branch/version_lock.json"
    try {
        return (Invoke-RestMethod -Uri $url -Headers @{ 'User-Agent' = 'dariusai-installer' }).version
    } catch {
        Fail "Could not fetch version_lock.json from $url — $($_.Exception.Message)"
    }
}

function Get-LocalVersion([string]$Root) {
    $lock = Join-Path $Root 'version_lock.json'
    if (-not (Test-Path $lock)) { return $null }
    try { return (Get-Content $lock -Raw | ConvertFrom-Json).version } catch { return $null }
}

# Sync latest files in via zip + robocopy. No git needed. Files the user
# owns are excluded so an update never overwrites the .env or wipes the
# venv — robocopy runs WITHOUT /MIR, so anything in the destination that
# isn't in the source stays.
function Update-FromZip([string]$Root) {
    $latest = Get-LatestVersion
    $current = Get-LocalVersion $Root
    Info "Latest version on $Branch : $latest"

    if ($current -eq $latest -and -not $Force) {
        Info "Already on $latest — nothing to download. Use -Force to resync anyway."
        return
    }
    if ($current) { Info "Updating $current -> $latest in $Root" }
    else { Info "Installing $latest to $Root" }

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('dariusai-install-' + [guid]::NewGuid().ToString('N'))
    $zipPath = Join-Path $tempRoot 'dariusai.zip'
    $extractPath = Join-Path $tempRoot 'extract'
    New-Item -ItemType Directory -Path $extractPath -Force | Out-Null

    try {
        $zipUrl = "https://github.com/$RepoOwner/$RepoName/archive/refs/heads/$Branch.zip"
        Info "Downloading $zipUrl"
        Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -Headers @{ 'User-Agent' = 'dariusai-installer' }

        Info 'Extracting archive'
        Expand-Archive -Path $zipPath -DestinationPath $extractPath -Force
        $repoFolder = Get-ChildItem -Path $extractPath -Directory | Select-Object -First 1
        if (-not $repoFolder) { Fail 'archive extracted empty' }

        Info "Syncing files into $Root"
        # robocopy /E copies everything, /R:2 /W:2 caps retries so a locked
        # file doesn't hang the install for minutes. NOT /MIR: destination
        # files that aren't in source are preserved — this is what keeps
        # the venv, brain.db and any user notes alive across updates.
        $excludeDirs = @('.git', '.venv', 'venv', '__pycache__', '.pytest_cache', 'node_modules', 'DariusAIWorkbench', '.dariusai-scratch')
        $excludeFiles = @('.env', '.env.local', 'brain.db', 'launch_error.log', 'dariusai-install-error.log')
        $args = @($repoFolder.FullName, $Root, '/E', '/R:2', '/W:2', '/NFL', '/NDL', '/NJH', '/NJS', '/NP',
                  '/XD') + $excludeDirs + @('/XF') + $excludeFiles
        & robocopy @args | Out-Null
        # robocopy exit codes 0-7 are success or informational; >=8 is a real error.
        if ($LASTEXITCODE -gt 7) { Fail "robocopy failed with exit code $LASTEXITCODE" }
        Info 'Files synced'
    } finally {
        if (Test-Path $tempRoot) { Remove-Item $tempRoot -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

function Check-RequiredFiles([string]$Root) {
    $listPath = Join-Path $Root 'install\required-files.json'
    if (-not (Test-Path $listPath)) { return }  # optional gate; skip when absent
    $missing = @()
    foreach ($rel in (Get-Content $listPath -Raw | ConvertFrom-Json)) {
        if (-not (Test-Path (Join-Path $Root $rel))) { $missing += $rel }
    }
    if ($missing.Count -gt 0) {
        $missing | ForEach-Object { Write-Host "  - $_" }
        Fail 'install is missing required files (listed above). Re-run with -Force to resync from GitHub.'
    }
}

function Ensure-Python() {
    Info 'Checking Python'
    $pyExe = $null
    $pyArgs = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $pyExe = 'py'
        $pyArgs = @("-$($MinPython[0])")
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $pyExe = 'python'
    } else {
        Fail "Python $($MinPython -join '.')+ not found on PATH. Install from https://python.org and re-run."
    }
    $raw = & $pyExe @pyArgs --version 2>&1 | Select-Object -First 1
    if ($LASTEXITCODE -ne 0 -or -not $raw) { Fail "Could not run Python via '$pyExe'." }
    if (-not ("$raw" -match 'Python\s+(\d+)\.(\d+)')) { Fail "Could not parse Python version: $raw" }
    $major = [int]$Matches[1]; $minor = [int]$Matches[2]
    if ($major -lt $MinPython[0] -or ($major -eq $MinPython[0] -and $minor -lt $MinPython[1])) {
        Fail "Python $($MinPython -join '.')+ required, found $major.$minor."
    }
    Info "python: $major.$minor via $pyExe"
    return @{ Exe = $pyExe; Args = $pyArgs }
}

function Initialize-Install([string]$Root, [hashtable]$Py) {
    Push-Location $Root
    try {
        # .env template only — never fabricate real keys.
        if (-not (Test-Path '.env') -and (Test-Path '.env.example')) {
            Copy-Item '.env.example' '.env'
            Info 'Created .env from .env.example (no keys — fill in if needed)'
        }

        $venv = Join-Path $Root '.venv'
        $venvPython = Join-Path $venv 'Scripts\python.exe'
        if (-not (Test-Path $venvPython)) {
            Info 'Creating virtual environment'
            & $Py.Exe @($Py.Args) -m venv $venv
            if (-not (Test-Path $venvPython)) { Fail "venv creation reported success but $venvPython does not exist." }
        } else {
            Info 'Virtual environment already present'
        }

        Info 'Installing package and dependencies'
        & $venvPython -m pip install --quiet --upgrade pip
        & $venvPython -m pip install --quiet -e "$Root[dev]"
        if ($LASTEXITCODE -ne 0) { Fail 'pip install failed. See output above.' }

        # Verify by importing, not by trusting pip's exit code.
        $installed = & $venvPython -c "import dariusai; print(dariusai.VERSION_DISPLAY)" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $installed) { Fail 'Package installed but cannot be imported.' }
        Info "installed $($installed.Trim())"

        Info 'Importing skill library into the brain'
        $addon = Join-Path $Root 'addon'
        if (-not (Test-Path $addon)) { Fail "addon directory missing at $addon — install incomplete." }
        $expected = (Get-ChildItem -Path (Join-Path $addon 'skills') -Filter 'SKILL.md' -Recurse -File).Count
        & $venvPython -m dariusai.cli import-addon --source $addon
        if ($LASTEXITCODE -ne 0) { Fail 'Skill import failed.' }
        Info "$expected SKILL.md files imported"

        if ($SkipShortcuts) {
            Info 'Skipping shortcuts (-SkipShortcuts)'
        } else {
            Info 'Creating Desktop / Start Menu shortcuts'
            & $venvPython -m dariusai.cli install-shortcuts
            if ($LASTEXITCODE -ne 0) { Warn 'shortcut creation failed; the app is still installed.' }
        }

        return $installed.Trim()
    } finally {
        Pop-Location
    }
}

try {
    $root = Resolve-InstallDir
    $script:ResolvedDest = $root
    Info "Target: $root"

    $py = Ensure-Python
    Update-FromZip $root
    Check-RequiredFiles $root
    $installed = Initialize-Install $root $py

    Write-Host ''
    Write-Host "DariusAI Harness $installed installed." -ForegroundColor Green
    Write-Host "  location: $root"
    Write-Host "  launch:   $root\.venv\Scripts\pythonw.exe $root\launch.pyw"
    Write-Host '            (or use the Desktop shortcut)'
    Write-Host ''
} catch {
    # Fail() already logged and paused. If we got here from something that
    # didn't route through Fail(), catch it too — the message would otherwise
    # disappear with the auto-closing PowerShell window.
    if (-not $_.Exception.Message.StartsWith('[DariusAI Installer]')) {
        Fail "install failed — $($_.Exception.Message)"
    }
    exit 1
}
