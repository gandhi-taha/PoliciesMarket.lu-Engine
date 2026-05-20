# One-command data bootstrap for Windows / PowerShell.
#
# Thin wrapper around `python -m app.workers.bootstrap`. Run from the
# engine repo root. Sequences:
#   1. Install Playwright + Chromium (one-time).
#   2. Run --learn per insurer (one-time per insurer).
#   3. Walk the profile grid via the assisted collector (per insurer).
#   4. Upload CSVs to the engine.
#   5. Hit /v1/recommend to verify offers come back.
#
# A human still has to sit at the browser through the assisted step.
# The script removes the chore of remembering and sequencing commands.
#
# Usage:
#   .\bootstrap.ps1                       # all enabled insurers
#   .\bootstrap.ps1 -Providers foyer      # just one
#   .\bootstrap.ps1 -SkipInstall          # skip Playwright install
#   .\bootstrap.ps1 -EngineUrl http://localhost:8000

param(
    [string]$Providers = "",
    [string]$EngineUrl = "http://localhost:8000",
    [switch]$SkipInstall,
    [switch]$NoVerify,
    [switch]$Reset
)

$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    $args = @("-m", "app.workers.bootstrap")
    if ($SkipInstall) { $args += "--skip-install" }
    if ($NoVerify)    { $args += "--no-verify" }
    if ($Reset)       { $args += "--reset" }
    if ($Providers)   { $args += "--providers"; $args += $Providers }
    $args += "--engine-url"; $args += $EngineUrl

    & python @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
