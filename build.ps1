param(
    [switch]$DebugBuild
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

# ── Step 2: Build LeagueCastAssist.exe (bundling updater.exe) ────────────────
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
