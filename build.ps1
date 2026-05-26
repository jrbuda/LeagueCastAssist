param(
    [switch]$DebugBuild
)

$ErrorActionPreference = "Stop"

python -m pip install -e ".[packaging]"

$args = @(
    "--name", "LeagueCastAssist",
    "--onefile",
    "--clean",
    "--icon", "megaphone-icon.ico",
    "--add-data", "megaphone-icon.png;."
)

if (-not $DebugBuild) {
    $args += "--windowed"
}

$args += "src/league_cast_assist/app.py"

python -m PyInstaller @args
