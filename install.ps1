<#
.SYNOPSIS
    Installs the DariusAI Harness from git, skill library included.

.DESCRIPTION
    Clones (or updates) the repository, builds a virtual environment, installs
    the package, imports the addon skill library and hooks into the brain, and
    creates the Desktop / Start Menu shortcuts.

    The script drives the package's own CLI (`dariusai import-addon`,
    `dariusai install-shortcuts`) rather than reimplementing any of it, so an
    install can never disagree with what the application does.

    Safe to re-run: an existing checkout is updated, never clobbered.

.PARAMETER Dest
    Where to install. Defaults to the folder this script was run from when that
    is already a checkout, otherwise $HOME\dariusai-harness.

.PARAMETER Repo
    Repository URL to clone from.

.PARAMETER Branch
    Branch to check out. Defaults to main.

.PARAMETER SkipShortcuts
    Do not create Desktop / Start Menu shortcuts.

.EXAMPLE
    ./install.ps1
    ./install.ps1 -Dest D:\apps\dariusai -Branch main
#>
[CmdletBinding()]
param(
    [string] $Dest,
    [string] $Repo = 'https://github.com/tattooinmtl/dariusai-harness.git',
    [string] $Branch = 'main',
    [switch] $SkipShortcuts
)

$ErrorActionPreference = 'Stop'

# Minimum the package declares in pyproject.toml. Kept here as two integers so
# the comparison is numeric — "3.9" -gt "3.11" is true for strings.
$MinPython = @(3, 11)

function Write-Step { param([string] $Message) Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok   { param([string] $Message) Write-Host "    $Message" -ForegroundColor DarkGray }
function Fail       { param([string] $Message) Write-Host "ERROR: $Message" -ForegroundColor Red; exit 1 }

# --- Prerequisites -----------------------------------------------------------
# Checked up front, so a missing tool is reported before anything is written to
# disk rather than half way through a clone.

Write-Step 'Checking prerequisites'

$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) { Fail 'git is not installed or not on PATH. Install it from https://git-scm.com and re-run.' }
Write-Ok "git: $((& git --version) -replace '^git version ', '')"

# Prefer the py launcher: it can select a version explicitly, where `python` on
# Windows may be the Store alias stub that exits without running anything.
$pythonExe = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonExe = 'py'
    $pythonArgs = @("-$($MinPython[0])")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonExe = 'python'
    $pythonArgs = @()
} else {
    Fail "Python $($MinPython -join '.')+ is not installed or not on PATH. Install it from https://python.org and re-run."
}

$versionText = & $pythonExe @pythonArgs -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
if ($LASTEXITCODE -ne 0 -or -not $versionText) { Fail "Could not run Python via '$pythonExe'." }

$found = $versionText.Trim().Split('.') | ForEach-Object { [int] $_ }
if ($found[0] -lt $MinPython[0] -or ($found[0] -eq $MinPython[0] -and $found[1] -lt $MinPython[1])) {
    Fail "Python $($MinPython -join '.')+ is required, found $versionText."
}
Write-Ok "python: $versionText"

# --- Destination -------------------------------------------------------------

if (-not $Dest) {
    $here = $PSScriptRoot
    $Dest = if ($here -and (Test-Path (Join-Path $here 'pyproject.toml'))) { $here }
            else { Join-Path $HOME 'dariusai-harness' }
}
$Dest = [System.IO.Path]::GetFullPath($Dest)

# --- Clone or update ---------------------------------------------------------
# The repository is private, so the clone needs credentials. `gh auth setup-git`
# hands git the CLI's existing login; without gh, git falls back to Windows
# Credential Manager, which prompts. Either way the failure below is explained
# rather than surfacing a bare git error.

if (Test-Path (Join-Path $Dest '.git')) {
    Write-Step "Updating existing checkout at $Dest"
    & git -C $Dest fetch --quiet origin $Branch
    if ($LASTEXITCODE -ne 0) { Fail "Could not fetch from origin. Check your network and GitHub access." }

    # Never clobber uncommitted work: refuse to move a dirty tree.
    $dirty = & git -C $Dest status --porcelain
    if ($dirty) {
        Write-Host 'WARNING: local changes present — leaving the working tree exactly as it is.' -ForegroundColor Yellow
        Write-Ok 'Skipping checkout/merge. Commit or stash, then re-run to update.'
    } else {
        & git -C $Dest checkout --quiet $Branch
        & git -C $Dest merge --ff-only --quiet "origin/$Branch"
        if ($LASTEXITCODE -ne 0) { Fail "Could not fast-forward to origin/$Branch." }
        Write-Ok "updated to $(& git -C $Dest rev-parse --short HEAD)"
    }
} else {
    Write-Step "Cloning $Repo"
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        & gh auth setup-git 2>$null   # no-op when gh is not logged in
    }
    & git clone --branch $Branch $Repo $Dest
    if ($LASTEXITCODE -ne 0) {
        Fail @"
Clone failed. This repository is private, so git needs credentials.
  - With the GitHub CLI:  gh auth login   (then re-run this script)
  - Otherwise, ensure Windows Credential Manager has a GitHub entry.
"@
    }
    Write-Ok "cloned into $Dest"
}

# --- Virtual environment -----------------------------------------------------

$venv = Join-Path $Dest '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'

if (Test-Path $venvPython) {
    Write-Step 'Virtual environment already present'
} else {
    Write-Step 'Creating virtual environment'
    & $pythonExe @pythonArgs -m venv $venv
    if (-not (Test-Path $venvPython)) { Fail "venv creation reported success but $venvPython does not exist." }
}
Write-Ok $venvPython

Write-Step 'Installing the package and its dependencies'
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -e "$Dest[dev]"
if ($LASTEXITCODE -ne 0) { Fail 'pip install failed. The output above says why.' }

# Verify by importing, not by trusting pip's exit code.
$installed = & $venvPython -c "import dariusai; print(dariusai.VERSION_DISPLAY)" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $installed) { Fail 'The package installed but cannot be imported.' }
Write-Ok "installed $($installed.Trim())"

# --- Skills ------------------------------------------------------------------
# import-addon defaults its source to <repo>/addon, but it resolves that
# relative to the *installed package*, so pass it explicitly against $Dest.

Write-Step 'Importing the skill library into the brain'
$addon = Join-Path $Dest 'addon'
if (-not (Test-Path $addon)) { Fail "No addon directory at $addon — the checkout is incomplete." }

$expected = (Get-ChildItem -Path (Join-Path $addon 'skills') -Filter 'SKILL.md' -Recurse -File).Count
& $venvPython -m dariusai.cli import-addon --source $addon
if ($LASTEXITCODE -ne 0) { Fail 'Skill import failed.' }
Write-Ok "$expected SKILL.md files present in the checkout"

# --- Shortcuts ---------------------------------------------------------------

if ($SkipShortcuts) {
    Write-Step 'Skipping shortcuts (-SkipShortcuts)'
} else {
    Write-Step 'Creating shortcuts'
    & $venvPython -m dariusai.cli install-shortcuts
    if ($LASTEXITCODE -ne 0) { Write-Host 'WARNING: shortcut creation failed; the app is still installed.' -ForegroundColor Yellow }
}

# --- Done --------------------------------------------------------------------

Write-Host ''
Write-Host "DariusAI Harness $($installed.Trim()) installed." -ForegroundColor Green
Write-Host "  location: $Dest"
Write-Host "  launch:   $venv\Scripts\pythonw.exe $Dest\launch.pyw"
Write-Host '            (or use the Desktop shortcut)'
Write-Host ''
