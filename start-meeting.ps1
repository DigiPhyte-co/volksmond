# start-meeting.ps1, CLI launcher for SA-Live-Transcribe
#
# Usage:
#   .\start-meeting.ps1
#   .\start-meeting.ps1 -Topic "Acme discovery"
#   .\start-meeting.ps1 -Topic "Acme discovery" -Tier cpu-strong
#   .\start-meeting.ps1 -Topic "Vleissentraal Q2" -Prompt "Vleissentraal, SubTropico, Hennie"
#
# Press Ctrl+C inside the running process to stop. Transcript flushes cleanly.

[CmdletBinding()]
param(
    [string]$Topic = "",
    [ValidateSet("auto","gpu","cpu-strong","cpu-mid")][string]$Tier = "auto",
    [string]$Language = "af",
    [string]$Prompt = "",
    [int]$ChunkSeconds = 0,
    [switch]$Offline,
    [switch]$KeepAudio,
    [switch]$SeedFromCalendar,
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$pyexe = Join-Path $env:LOCALAPPDATA "sa-live-transcribe\.venv\Scripts\python.exe"

if (-not (Test-Path $pyexe)) {
    Write-Host ""
    Write-Host "  [error] Python venv not found at:" -ForegroundColor Red
    Write-Host "    $pyexe"
    Write-Host ""
    Write-Host "  This is a fresh machine. Run 'First-time setup.bat' first" -ForegroundColor Yellow
    Write-Host "  (in this same folder)."
    exit 1
}

if (-not $Output) {
    $ts = Get-Date -Format "yyyy-MM-dd-HHmm"
    if ($Topic) {
        $slug = $Topic.ToLower() -replace '[^a-z0-9]+', '-'
        $slug = $slug.Trim('-')
        if (-not $slug) { $slug = "session" }
        $Output = Join-Path $here "sessions\$ts-$slug.md"
    } else {
        $Output = Join-Path $here "sessions\$ts-session.md"
    }
}

$sessionsDir = Split-Path $Output -Parent
if (-not (Test-Path $sessionsDir)) {
    New-Item -ItemType Directory -Force -Path $sessionsDir | Out-Null
}

$cliArgs = @(
    "-m", "live_transcribe",
    "--output", $Output,
    "--language", $Language,
    "--tier", $Tier
)
if ($Prompt)             { $cliArgs += @("--prompt", $Prompt) }
if ($ChunkSeconds -gt 0) { $cliArgs += @("--chunk-seconds", $ChunkSeconds) }
if ($Offline)            { $cliArgs += "--offline" }
if ($KeepAudio)          { $cliArgs += "--keep-audio" }
if ($SeedFromCalendar)   { $cliArgs += "--seed-from-calendar" }

Push-Location $here
$exitCode = 0
try {
    & $pyexe @cliArgs
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host ""
if (Test-Path $Output) {
    Write-Host "  Transcript saved:" -ForegroundColor Green
    Write-Host "  $Output"
} else {
    Write-Host "  No transcript file was written." -ForegroundColor Yellow
}

exit $exitCode
