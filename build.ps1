param(
    [switch]$DebugBuild
)

$ErrorActionPreference = "Stop"

python -m pip install -e ".[packaging]"

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
python -m PyInstaller @updaterArgs

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
    "--add-data", "dist/updater.exe;."
)

if (-not $DebugBuild) {
    $mainArgs += "--windowed"
}

$mainArgs += "src/league_cast_assist/app.py"

python -m PyInstaller @mainArgs
