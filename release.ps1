# release.ps1 - Publish a new Volksmond version online (surgical, repeatable, automated).
#
# Say "ship the new version" to Claude (the ship-volksmond skill) and it runs this end to
# end. Or run it yourself. What it does, in order:
#   1. Reads APP_VERSION from licensing.py (single source of truth; build-app.ps1 reads the
#      same line, so the installer name always matches).
#   2. Finds Volksmond-Setup-<ver>.exe in the repo root. -Build (re)builds it first.
#   3. SHA-256 of the exact bytes to be published.
#   4. VirusTotal: uploads the installer, waits for the scan, records the permalink +
#      detection count. The key comes from $env:VT_API_KEY or Doppler infra-ops/prd; a real
#      run FAILS without it unless you pass -SkipScan (which reuses the deterministic report
#      link for the exact bytes, no fresh scan).
#   5. Writes site/latest.json (update manifest) and site/trust.json (version + hash + VT,
#      for trust.html to read) - both BOM-free.
#   6. Uploads to the R2 bucket served at dl.volksmond.com (via doppler run -p infra-ops -c prd,
#      which injects the Cloudflare R2 credentials):
#        Volksmond-Setup-<ver>.exe      versioned installer (immutable, cached forever)
#        Volksmond-Setup-latest.exe     stable alias so the site's DOWNLOAD_URL never changes
#        latest.json / models.json / trust.json   the three manifests (short cache)
#      A release touches ONLY the release host. The marketing site is never redeployed.
#   7. Verifies the live manifest, prints the summary.
#
# One-time setup: see RELEASE.md ("One-time R2 setup" + "VirusTotal API key").
#
#   .\release.ps1              # publish the version currently in licensing.py
#   .\release.ps1 -Build       # rebuild the installer first
#   .\release.ps1 -SkipScan    # skip the fresh VirusTotal scan; reuse the report link for these bytes
#   .\release.ps1 -DryRun      # everything except VirusTotal + the R2 uploads
[CmdletBinding()]
param(
    # The canonical release bucket: the PRE-EXISTING `volksmond` bucket in the EU JURISDICTION,
    # with dl.volksmond.com (canonical) and dl.volksmond.digiphyte.com (legacy alias) bound and
    # Active. NOT the redundant standard-jurisdiction `volksmond-dl` bucket, which is pending
    # deletion. See RELEASE.md.
    [string]$Bucket = "volksmond",
    [string]$Domain = "dl.volksmond.com",
    # R2 jurisdiction. The volksmond bucket is an EU-JURISDICTION bucket, and wrangler cannot
    # find a jurisdictioned bucket without this flag on every object op (nor a standard bucket
    # with it). Keep "eu" unless targeting a standard-jurisdiction bucket, then pass "".
    [string]$Jurisdiction = "eu",
    # Where the in-app "Download" link (latest.json "url") sends the user. MUST be an https URL on
    # digiphyte.com or a *.digiphyte.com subdomain (or github.com): the shipped app (app.js
    # openUpdateLink allowlist, live_transcribe/web/static/app.js:2166-2176) REJECTS any other
    # host, so a bare volksmond.com link is refused by every installed client. The host
    # volksmond.digiphyte.com 308-redirects to volksmond.com, which the browser follows AFTER the
    # app has opened the link. Do not "fix" this back to volksmond.com. See RELEASE.md.
    [string]$SiteUrl = "https://volksmond.digiphyte.com/",
    [string]$Notes,
    [string]$AccountId,
    # Optional: reuse a specific existing VirusTotal report URL instead of the deterministic one.
    # (Named distinctly from the internal $vtUrl: PowerShell variable names are case-insensitive.)
    [string]$ReuseVtUrl,
    [switch]$Build,
    # Skip the fresh VirusTotal scan and reuse the report link for these exact bytes.
    [switch]$SkipScan,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
if ($AccountId) { $env:CLOUDFLARE_ACCOUNT_ID = $AccountId }

function Fail($msg) { Write-Host "  $msg" -ForegroundColor Red; exit 1 }

# Resolve wrangler at runtime on THIS machine (no hardcoded paths). Prefer a wrangler already
# on PATH; otherwise run it via npx, which ships with Node and needs no global install or PATH
# entry. Returns the leading command tokens (e.g. @("npx","--yes","wrangler")), or $null.
function Get-WranglerBase {
    $cmd = Get-Command wrangler -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { return @($cmd.Source) }
    $npx = Get-Command npx -ErrorAction SilentlyContinue
    if ($npx -and $npx.Source) { return @($npx.Source, "--yes", "wrangler") }
    return $null
}

# Resolve the VirusTotal API key WITHOUT ever printing it: $env:VT_API_KEY first, then the
# Doppler secret in infra-ops/prd (may not exist yet). Returns $null if neither is present.
function Resolve-VtKey {
    if ($env:VT_API_KEY) { return $env:VT_API_KEY }
    $d = Get-Command doppler -ErrorAction SilentlyContinue
    if ($d -and $d.Source) {
        try {
            $k = (& $d.Source secrets get VT_API_KEY -p infra-ops -c prd --plain 2>$null | Out-String).Trim()
            if ($k) { return $k }
        } catch { }
    }
    return $null
}

# --- VirusTotal: upload, wait for the scan, return @{ url; detections; status } ----------
# $sha is lowercase hex (the GUI permalink form). $key is resolved by the caller and never printed.
function Invoke-VirusTotal($file, $sha, $key) {
    $permalink = "https://www.virustotal.com/gui/file/$sha"
    $headers = @{ "x-apikey" = $key }

    # Already known to VT (same bytes uploaded before)? Then just read its stats.
    try {
        $existing = Invoke-RestMethod -Uri "https://www.virustotal.com/api/v3/files/$sha" -Headers $headers -Method Get -TimeoutSec 30
        $s = $existing.data.attributes.last_analysis_stats
        $total = $s.malicious + $s.suspicious + $s.harmless + $s.undetected
        Write-Host "  VirusTotal: already scanned, $($s.malicious) / $total flagged. $permalink" -ForegroundColor Green
        return @{ url = $permalink; detections = "$($s.malicious) / $total"; status = "completed" }
    } catch { }  # 404 = not seen yet; fall through to upload

    try {
        $mb = [math]::Round((Get-Item $file).Length / 1MB)
        Write-Host "  VirusTotal: requesting an upload URL and uploading (~$mb MB)..." -ForegroundColor Gray
        $uploadUrl = (Invoke-RestMethod -Uri "https://www.virustotal.com/api/v3/files/upload_url" -Headers $headers -Method Get -TimeoutSec 30).data
        # curl.exe streams the file (Invoke-RestMethod would buffer ~100 MB in memory). NOTE: curl.exe,
        # not curl - in Windows PowerShell 'curl' is an alias for Invoke-WebRequest.
        $respStr = (curl.exe -s -X POST $uploadUrl -H "x-apikey: $key" -F "file=@$file" | Out-String)
        if ($LASTEXITCODE -ne 0) { throw "curl upload failed (rc=$LASTEXITCODE)" }
        $analysisId = ($respStr | ConvertFrom-Json).data.id
        if (-not $analysisId) { throw "no analysis id in the upload response" }
    } catch {
        Write-Host "  VirusTotal: upload failed ($($_.Exception.Message)); results will appear at $permalink once scanned." -ForegroundColor Yellow
        return @{ url = $permalink; detections = $null; status = "upload-failed" }
    }

    $deadline = (Get-Date).AddMinutes(4)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 15
        try {
            $a = Invoke-RestMethod -Uri "https://www.virustotal.com/api/v3/analyses/$analysisId" -Headers $headers -Method Get -TimeoutSec 30
        } catch { continue }
        if ($a.data.attributes.status -eq "completed") {
            $s = $a.data.attributes.stats
            $total = $s.malicious + $s.suspicious + $s.harmless + $s.undetected
            Write-Host "  VirusTotal: $($s.malicious) / $total engines flagged it. $permalink" -ForegroundColor Green
            return @{ url = $permalink; detections = "$($s.malicious) / $total"; status = "completed" }
        }
        Write-Host "  VirusTotal: scanning..." -ForegroundColor Gray
    }
    Write-Host "  VirusTotal: still scanning after 4 min; results will appear at $permalink." -ForegroundColor Yellow
    return @{ url = $permalink; detections = $null; status = "pending" }
}

function Write-JsonNoBom($path, $obj) {
    # UTF-8 WITHOUT BOM: updatecheck.py does json.loads(resp.read().decode("utf-8")), which a
    # BOM would break. Set-Content -Encoding UTF8 writes a BOM on PS 5.1, so write it by hand.
    $json = ($obj | ConvertTo-Json -Depth 6)
    [System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
}

# --- 1. Version (same read as build-app.ps1) --------------------------------------------
$licPy = Join-Path $here "live_transcribe\licensing.py"
if (-not (Test-Path $licPy)) { Fail "licensing.py not found; run from the project root." }
$verLine = Get-Content $licPy | Where-Object { $_ -match 'APP_VERSION\s*=' } | Select-Object -First 1
if ($verLine -notmatch '"([0-9]+\.[0-9]+\.[0-9]+)"') { Fail "Could not read APP_VERSION from licensing.py." }
$ver = $Matches[1]
Write-Host ""
Write-Host "  Releasing Volksmond $ver" -ForegroundColor Cyan

# --- 2. Installer ------------------------------------------------------------------------
$exe = Join-Path $here "Volksmond-Setup-$ver.exe"
if ($Build -or -not (Test-Path $exe)) {
    if ($Build) { Write-Host "  -Build: building the connected installer..." -ForegroundColor Gray }
    else { Write-Host "  Installer not found for $ver; building it..." -ForegroundColor Gray }
    & (Join-Path $here "build-app.ps1") -Editions connected
    if ($LASTEXITCODE -ne 0) { Fail "build-app.ps1 failed (rc=$LASTEXITCODE)." }
}
if (-not (Test-Path $exe)) { Fail "Installer still missing: $exe" }
$exeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "  Installer: $exe  ($exeMb MB)" -ForegroundColor Green

# --- 3. SHA-256 --------------------------------------------------------------------------
$shaUpper = (Get-FileHash -Path $exe -Algorithm SHA256).Hash   # Get-FileHash returns UPPERCASE hex
$shaLower = $shaUpper.ToLower()                                # VirusTotal GUI permalink form
Write-Host "  SHA-256:   $shaUpper" -ForegroundColor Green

# --- 4. VirusTotal ------------------------------------------------------------------------
# The permalink is deterministic from the hash, so trust.json always carries a valid link even
# when no fresh scan runs. A scan only adds the detection count for the console summary.
$vtUrl = "https://www.virustotal.com/gui/file/$shaLower"
$vtDet = $null
if (-not $DryRun) {
    if ($SkipScan) {
        if ($ReuseVtUrl) { $vtUrl = $ReuseVtUrl }
        Write-Host "  VirusTotal: -SkipScan; reusing $vtUrl (no fresh scan)." -ForegroundColor Yellow
    } else {
        $vtKey = Resolve-VtKey
        if (-not $vtKey) { Fail "VirusTotal key not found. Set `$env:VT_API_KEY, or add it to Doppler (doppler secrets set VT_API_KEY -p infra-ops -c prd), or pass -SkipScan to reuse the existing report for these exact bytes." }
        $vt = Invoke-VirusTotal $exe $shaLower $vtKey
        if ($vt) { $vtUrl = $vt.url; $vtDet = $vt.detections }
    }
}

# --- 5. Write site/latest.json + site/trust.json (no BOM) --------------------------------
$manifestPath = Join-Path $here "site\latest.json"
$trustPath    = Join-Path $here "site\trust.json"
$modelsPath   = Join-Path $here "site\models.json"
if (-not (Test-Path $modelsPath)) { Fail "site/models.json not found; expected next to latest.json." }
if (-not $PSBoundParameters.ContainsKey('Notes')) {
    if (Test-Path $manifestPath) { try { $Notes = (Get-Content $manifestPath -Raw | ConvertFrom-Json).notes } catch { $Notes = "" } }
}
$downloadUrl = "https://$Domain/Volksmond-Setup-$ver.exe"   # versioned, matches the hash
$latestAlias = "https://$Domain/Volksmond-Setup-latest.exe" # stable, for the site DOWNLOAD_URL

Write-JsonNoBom $manifestPath ([ordered]@{ version = $ver; url = $SiteUrl; notes = $Notes })
# trust.json: exact contract shared with trust.html. Do not add, rename or reorder fields.
# sha256 is UPPERCASE hex; virustotal is the lowercase-hash GUI permalink.
Write-JsonNoBom $trustPath ([ordered]@{
    version    = $ver
    filename   = "Volksmond-Setup-$ver.exe"
    sha256     = $shaUpper
    virustotal = $vtUrl
    published  = (Get-Date -Format 'yyyy-MM-dd')
})
Write-Host "  Wrote:     site\latest.json + site\trust.json (version=$ver)" -ForegroundColor Green

# --- 6. Upload to R2 ---------------------------------------------------------------------
if ($DryRun) {
    Write-Host ""
    Write-Host "  -DryRun: skipping VirusTotal + the R2 uploads. Would upload:" -ForegroundColor Yellow
    "Volksmond-Setup-$ver.exe", "Volksmond-Setup-latest.exe", "latest.json", "models.json", "trust.json" |
        ForEach-Object { Write-Host "    $Bucket/$_" -ForegroundColor Yellow }
} else {
    $wBase = Get-WranglerBase
    if (-not $wBase) { Fail "wrangler not found (checked PATH and npx). Install it with: npm i -g wrangler" }
    $doppler = (Get-Command doppler -ErrorAction SilentlyContinue).Source
    if (-not $doppler) { Fail "Doppler CLI not found. R2 uploads inject the Cloudflare R2 credentials via 'doppler run -p infra-ops -c prd'. Install Doppler and log in." }
    Write-Host "  Uploading via doppler run -p infra-ops -c prd -- $($wBase -join ' ')" -ForegroundColor DarkGray

    # Clear DOPPLER_TOKEN for these calls so doppler resolves the infra-ops/prd config from the CLI
    # login, not a stray service token scoped to another project (mirrors `env -u DOPPLER_TOKEN`).
    $savedToken = $env:DOPPLER_TOKEN
    if ($null -ne $savedToken) { Remove-Item Env:DOPPLER_TOKEN }
    try {
        function Put-R2($key, $file, $ct, $cc) {
            Write-Host "  Uploading $key ..." -ForegroundColor Gray
            $j = @(); if ($Jurisdiction) { $j = @("--jurisdiction", $Jurisdiction) }
            $wr = @("r2", "object", "put", "$Bucket/$key", "--file", $file, "--remote", "--content-type", $ct, "--cache-control", $cc) + $j
            $dArgs = @("run", "-p", "infra-ops", "-c", "prd", "--") + $wBase + $wr
            & $doppler @dArgs
            if ($LASTEXITCODE -ne 0) { Fail "Upload of $key failed (rc=$LASTEXITCODE). Check bucket '$Bucket', jurisdiction '$Jurisdiction', and that Doppler infra-ops/prd holds the Cloudflare R2 credentials." }
        }
        # Versioned installer: safe to cache forever. Everything overwritten each release: short cache.
        Put-R2 "Volksmond-Setup-$ver.exe"   $exe          "application/octet-stream" "public, max-age=31536000, immutable"
        Put-R2 "Volksmond-Setup-latest.exe" $exe          "application/octet-stream" "public, max-age=300"
        Put-R2 "latest.json"                $manifestPath "application/json"         "public, max-age=300"
        Put-R2 "models.json"                $modelsPath   "application/json"         "public, max-age=300"
        Put-R2 "trust.json"                 $trustPath    "application/json"         "public, max-age=300"
        Write-Host "  Uploaded 5 objects to $Bucket." -ForegroundColor Green
    } finally {
        if ($null -ne $savedToken) { $env:DOPPLER_TOKEN = $savedToken }
    }
}

# --- 7. Verify + summary -----------------------------------------------------------------
$manifestUrl = "https://$Domain/latest.json"
if (-not $DryRun) {
    try {
        $live = (Invoke-WebRequest $manifestUrl -UseBasicParsing -TimeoutSec 20).Content | ConvertFrom-Json
        if ($live.version -eq $ver) { Write-Host "  Verified:  $manifestUrl reports $ver" -ForegroundColor Green }
        else { Write-Host "  WARNING: $manifestUrl reports '$($live.version)', expected '$ver'. Check the domain binding / cache." -ForegroundColor Yellow }
    } catch {
        Write-Host "  WARNING: could not fetch $manifestUrl yet ($($_.Exception.Message))." -ForegroundColor Yellow
        Write-Host "           If this is the first release, confirm dl.volksmond.com is connected to the bucket." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "  ===============  RELEASE $ver PUBLISHED  ===============" -ForegroundColor Cyan
Write-Host "  Download (versioned) : $downloadUrl"
Write-Host "  Download (stable)    : $latestAlias"
Write-Host "  SHA-256              : $shaUpper"
if ($vtUrl) {
    Write-Host "  VirusTotal           : $vtUrl"
    if ($vtDet) { Write-Host "  VT detections        : $vtDet" }
}
Write-Host "  Manifest / trust     : $manifestUrl  |  https://$Domain/trust.json"
Write-Host ""
Write-Host "  trust.html reads trust.json, and the site's DOWNLOAD_URL is the stable alias, so"
Write-Host "  the marketing site needs NO per-release change. (One-time site wiring: RELEASE.md.)" -ForegroundColor Gray
Write-Host ""
