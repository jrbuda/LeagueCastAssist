param(
    [switch]$DebugBuild,
    [switch]$Portable
)

$ErrorActionPreference = "Stop"

# Prefer the repo virtualenv (what the docs instruct) and fall back to a
# python on PATH for machines that use a global interpreter.
$Python = "python"
if (Test-Path -LiteralPath ".venv\Scripts\python.exe") {
    $Python = ".venv\Scripts\python.exe"
}

& $Python -m pip install -e ".[packaging]"

# ── Step 1: Build updater.exe ────────────────────────────────────────────────
Write-Host "Building updater.exe..."
$updaterArgs = @(
    "--name", "updater",
    "--onefile",
    "--clean",
    "--windowed",
    "--distpath", "dist",
    "src/leaguecastassist_updater/updater.py"
)
& $Python -m PyInstaller @updaterArgs

if (-not (Test-Path -LiteralPath "dist/updater.exe")) {
    Write-Error "updater.exe was not produced; aborting."
    exit 1
}

# ── Step 2: Build the app ────────────────────────────────────────────────────
# -Portable builds a folder (--onedir): every file lives beside the exe and
# nothing is unpacked into %TEMP% at launch. That is the right shape for a
# portable install and for local QA. The default single-exe build (--onefile)
# is what the GitHub-release updater swaps in place, but it must extract itself
# to %TEMP%\_MEI<pid> before any app code runs.
if ($Portable) {
    Write-Host "Building dist/LeagueCastAssist-portable (onedir, no %TEMP% extraction)..."
    $portableArgs = @(
        "--name", "LeagueCastAssist-portable",
        "--onedir",
        "--clean",
        "--icon", "megaphone-icon.ico",
        "--add-data", "megaphone-icon.png;.",
        "--add-data", "dist/updater.exe;.",
        "--add-data", "src/league_cast_assist/music/seed/music.db;.",
        "--distpath", "dist"
    )
    if (-not $DebugBuild) {
        $portableArgs += "--windowed"
    }
    $portableArgs += "src/league_cast_assist/app.py"
    & $Python -m PyInstaller @portableArgs
    Write-Host "Portable build: dist\LeagueCastAssist-portable\LeagueCastAssist-portable.exe"
    exit 0
}

Write-Host "Building LeagueCastAssist.exe..."
$mainArgs = @(
    "--name", "LeagueCastAssist",
    "--onefile",
    "--clean",
    "--icon", "megaphone-icon.ico",
    "--add-data", "megaphone-icon.png;.",
    "--add-data", "dist/updater.exe;.",
    "--add-data", "src/league_cast_assist/music/seed/music.db;."
)

if (-not $DebugBuild) {
    $mainArgs += "--windowed"
}

$mainArgs += "src/league_cast_assist/app.py"

& $Python -m PyInstaller @mainArgs
Write-Host "Single-exe build: dist\LeagueCastAssist.exe"
