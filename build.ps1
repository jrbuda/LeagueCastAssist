param(
    [switch]$DebugBuild
)

$ErrorActionPreference = "Stop"

python -m pip install -e ".[packaging]"

$args = @(
    "--name", "LeagueCastAssist",
    "--onefile",
    "--clean"
)

if (-not $DebugBuild) {
    $args += "--windowed"
}

$args += "src/league_cast_assist/app.py"

python -m PyInstaller @args
