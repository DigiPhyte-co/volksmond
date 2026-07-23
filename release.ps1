# release.ps1 - Publish a new Volksmond version online (surgical, repeatable, automated).
#
# Say "release volksmond" to Claude (the release-volksmond skill) and it runs this end to
# end. Or run it yourself. What it does, in order:
#   1. Reads APP_VERSION from licensing.py (single source of truth; build-app.ps1 reads the
#      same line, so the installer name always matches).
#   2. Finds Volksmond-Setup-<ver>.exe in the repo root. -Build (re)builds it first.
#   3. SHA-256 of the exact bytes to be published.
#   4. Malware scan: Microsoft Defender, fully local and MANDATORY (MpCmdRun.exe custom file
#      scan; a threat fails the release loudly; engine + definitions versions recorded in
#      trust.json). VirusTotal is OPT-IN only: -VirusTotal runs an API scan (needs VT_API_KEY
#      and a VT licence appropriate to commercial use), or -VtUrl records a manually obtained
#      report link. No VT network call happens by default. See RELEASE.md "Malware scanning".
#   5. Fetches the LIVE latest.json + trust.json from the release host as the merge base
#      (site/ is untracked and machine-local, so the local copies may be stale), merges this
#      platform's fields into that live content, then writes site/latest.json (update
#      manifest) and site/trust.json (version + hash + scan record, for trust.html to read) -
#      both BOM-free. A failed live fetch fails the publish; -DryRun warns and falls back to
#      the local site/ files. A publish-time byte-preservation gate proves the merge did not
#      touch the OTHER platform's fields.
#   6. Uploads to the R2 bucket served at dl.volksmond.com (via doppler run -p infra-ops -c prd,
#      which injects the Cloudflare R2 credentials):
#        Volksmond-Setup-<ver>.exe      versioned installer (immutable, cached forever)
#        Volksmond-Setup-latest.exe     stable alias so the site's DOWNLOAD_URL never changes
#        latest.json / models.json / trust.json   the three manifests (short cache)
#      A release touches ONLY the release host. The marketing site is never redeployed.
#   7. Verifies the live manifest, prints the summary.
#
# One-time setup: see RELEASE.md ("R2 setup" + "Malware scanning").
#
#   .\release.ps1                 # publish the version currently in licensing.py (Defender scan included)
#   .\release.ps1 -Build          # rebuild the installer first
#   .\release.ps1 -VirusTotal     # ALSO scan on VirusTotal (licensed API key required; see RELEASE.md)
#   .\release.ps1 -VtUrl <link>   # record a manually obtained VirusTotal report link (no API call)
#   .\release.ps1 -TrustOnly -VtUrl <link>   # regenerate + upload ONLY trust.json (attach a VT
#                                            # link to an already-shipped release; no exe upload)
#   .\release.ps1 -DryRun         # everything local (incl. the Defender scan); NO uploads, no VT
#
# MAC LANE (docs/mac-port-plan.md section 2.7 + WP-G). CI builds, signs and notarises the DMG
# (WP-F); publishing stays on this machine. -MacDmg hashes the DMG, runs the SAME mandatory
# local Defender scan over it (notarisation is the primary mac attestation; the Defender pass
# is belt-and-braces since the DMG is published from this Windows machine anyway), merges the
# "mac" keys into the LIVE latest.json + trust.json WITHOUT touching any Windows field, and
# uploads Volksmond-<ver>.dmg first, then both manifests, then the Volksmond-latest.dmg alias
# last (so nothing public ever points at bytes that are not up yet). Windows and Mac releases
# ship independently: each lane only writes its own platform's keys.
#
#   .\release.ps1 -MacDmg <path> -NotarisationJson <path>   # publish the mac DMG
#   .\release.ps1 -MacDmg <path> -DryRun                    # local only; prints the manifest diff
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
    # OPT-IN: run a VirusTotal API scan. Requires VT_API_KEY (env or Doppler infra-ops/prd) AND
    # a VT licence appropriate to commercial use; the free public API is ruled out for business
    # workflows (Sean, 2026-07-11). See RELEASE.md "Malware scanning".
    [switch]$VirusTotal,
    # OPT-IN: record a manually obtained VirusTotal report link in trust.json (no API call).
    # The internal variable is $vtLink because PS variable names are case-insensitive.
    [string]$VtUrl,
    # Regenerate and upload ONLY trust.json (hash + Defender scan still run against the existing
    # installer; the exe, latest.json and models.json are NOT uploaded). For attaching a
    # VirusTotal link to an already-shipped release: .\release.ps1 -TrustOnly -VtUrl <link>.
    [switch]$TrustOnly,
    [switch]$Build,
    # MAC LANE: path to the signed, notarised, stapled DMG from CI (mac-release workflow, WP-F).
    # Switches the whole run to the mac lane: hash + Defender scan of the DMG, merge the "mac"
    # keys into site/latest.json + site/trust.json (every Windows field passes through
    # untouched), upload the DMG versioned + as the Volksmond-latest.dmg alias + both manifests.
    # Mutually exclusive with -Build / -TrustOnly / -VirusTotal / -VtUrl.
    [string]$MacDmg,
    # MAC LANE: the notarisation sidecar CI emits next to the DMG, JSON of the shape
    # {submission_id, status, date, version, sha256}. version must equal APP_VERSION and
    # sha256 must equal the SHA-256 of the DMG bytes (ties the sidecar to THIS release and
    # THIS artifact). Recorded in trust.json's mac entry as the notarisation attestation.
    # REQUIRED for a real mac publish; may be omitted only with -DryRun.
    [string]$NotarisationJson,
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
    # doppler exec()s the resolved command directly (CreateProcess), so a PowerShell shim
    # (.ps1) is not runnable ("%1 is not a valid Win32 application"); npm installs a .cmd
    # shim beside it, prefer that. Same applies to npx itself.
    $cmd = Get-Command wrangler -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) {
        $src = $cmd.Source
        if ($src -like "*.ps1") {
            $sibling = [System.IO.Path]::ChangeExtension($src, ".cmd")
            if (Test-Path $sibling) { return @($sibling) }
        } else {
            return @($src)
        }
    }
    $npx = Get-Command npx -ErrorAction SilentlyContinue
    if ($npx -and $npx.Source) {
        $nsrc = $npx.Source
        if ($nsrc -like "*.ps1") {
            $nsibling = [System.IO.Path]::ChangeExtension($nsrc, ".cmd")
            if (Test-Path $nsibling) { return @($nsibling, "--yes", "wrangler") }
        } else {
            return @($nsrc, "--yes", "wrangler")
        }
    }
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

# --- Microsoft Defender: the MANDATORY local scan of the exact bytes to be published ------
# MpCmdRun -Scan exit codes: 0 = no threat found, 2 = threat found. Anything else = the scan
# itself failed; either way we do not publish. Returns the trust.json "defender" object.
function Invoke-DefenderScan($file, $label = "the installer") {
    $mp = Join-Path $env:ProgramFiles "Windows Defender\MpCmdRun.exe"
    if (-not (Test-Path $mp)) {
        # Newer Defender platform versions live under ProgramData\...\Platform\<version>\
        $mp = $null
        $plat = Join-Path $env:ProgramData "Microsoft\Windows Defender\Platform"
        if (Test-Path $plat) {
            $latest = Get-ChildItem $plat -Directory |
                Where-Object { $_.Name -match '^\d' } |
                Sort-Object { [version]($_.Name -replace '[^\d.].*$','') } -Descending |
                Select-Object -First 1
            if ($latest) {
                $cand = Join-Path $latest.FullName "MpCmdRun.exe"
                if (Test-Path $cand) { $mp = $cand }
            }
        }
    }
    if (-not $mp) { Fail "MpCmdRun.exe not found (checked `$env:ProgramFiles\Windows Defender and the Defender Platform dir). Defender is the mandatory release scan; not publishing unscanned." }
    Write-Host "  Defender:  scanning $label (MpCmdRun -Scan -ScanType 3)..." -ForegroundColor Gray
    & $mp -Scan -ScanType 3 -File $file | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -eq 2) { Fail "DEFENDER FOUND A THREAT in $file. DO NOT PUBLISH. Re-run for detail: & `"$mp`" -Scan -ScanType 3 -File `"$file`"" }
    if ($rc -ne 0) { Fail "Defender scan did not complete cleanly (rc=$rc); not publishing an unscanned installer." }
    $engine = ""; $defs = ""
    try {
        $s = Get-MpComputerStatus
        $engine = "$($s.AMEngineVersion)"; $defs = "$($s.AntivirusSignatureVersion)"
    } catch { }
    Write-Host "  Defender:  clean (engine $engine, definitions $defs)" -ForegroundColor Green
    return [ordered]@{
        result      = "clean"
        engine      = $engine
        definitions = $defs
        scanned     = (Get-Date -Format 'yyyy-MM-dd')
    }
}

# --- VirusTotal: upload, wait for the scan, return @{ url; detections; status } ----------
# OPT-IN only (-VirusTotal); see RELEASE.md "Malware scanning" for the licensing ruling.
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
    $json = ($obj | ConvertTo-Json -Depth 10)
    [System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
}

# --- Live-manifest merge base --------------------------------------------------------------
# site\ is untracked and machine-local, so the local latest.json / trust.json can be stale or
# missing (another clone or the other platform's lane may have published since this machine
# last wrote them). BOTH lanes therefore merge this platform's fields into the LIVE manifest
# fetched from the release host, never into the local copy. Publish: a failed fetch hard-fails
# the release. -DryRun: a failed fetch warns and falls back to the local site\ file so the
# lane stays testable offline. Returns the raw JSON string of the merge base.
function Get-MergeBaseRaw($name, $localPath) {
    $url = "https://$Domain/$name"
    try {
        $raw = (Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 15).Content
        if (-not $raw) { throw "empty response" }
        $null = $raw | ConvertFrom-Json   # must be JSON, or the merge below writes garbage
        Write-Host "  Merge base: live $url" -ForegroundColor Green
        return $raw
    } catch {
        if (-not $DryRun) {
            Fail "Could not fetch the live $url ($($_.Exception.Message)). The merge base for a publish is the LIVE manifest, not the machine-local site\$name (which may be stale). Fix connectivity / the domain binding and retry; not publishing."
        }
        if (Test-Path $localPath) {
            Write-Host "  Merge base: live fetch of $url failed ($($_.Exception.Message)); -DryRun falls back to the LOCAL site\$name." -ForegroundColor Yellow
            return Get-Content $localPath -Raw
        }
        Fail "-DryRun: live fetch of $url failed AND there is no local site\$name to fall back to. Fetch it once: Invoke-WebRequest $url -OutFile site\$name"
    }
}

# JSON of a manifest projected onto (keep only) or away from (drop) the "mac" key, for the
# byte-preservation gate below. Both sides go through the same ConvertFrom/ConvertTo-Json
# round trip, so formatting differences cancel out and only real value/shape changes remain.
function Get-ProjectedJson($raw, $keepMacOnly) {
    if (-not $raw) { return "" }
    $o = $raw | ConvertFrom-Json
    $proj = [ordered]@{}
    foreach ($p in $o.PSObject.Properties) {
        $isMac = ($p.Name -eq "mac")
        if (($keepMacOnly -and $isMac) -or ((-not $keepMacOnly) -and (-not $isMac))) { $proj[$p.Name] = $p.Value }
    }
    return ($proj | ConvertTo-Json -Depth 10)
}

# PUBLISH-TIME byte-preservation gate: re-serialise the merge base and the merged result
# WITHOUT this platform's key(s); any difference means the merge touched the OTHER platform's
# fields. Publish: fail the release, naming the manifest. -DryRun: print the before/after for
# inspection and continue (it is the dry run's job to surface this).
function Assert-OtherPlatformPreserved($beforeRaw, $afterRaw, $what, $keepMacOnly) {
    $before = Get-ProjectedJson $beforeRaw $keepMacOnly
    $after  = Get-ProjectedJson $afterRaw  $keepMacOnly
    $label = if ($keepMacOnly) { "mac" } else { "Windows" }
    if ($before -eq $after) {
        Write-Host "    $($what): $label fields byte-identical." -ForegroundColor Green
        return
    }
    Write-Host "    $($what): $label FIELDS CHANGED by the merge:" -ForegroundColor Red
    Write-Host "    --- before ---`n$before" -ForegroundColor Red
    Write-Host "    --- after ----`n$after" -ForegroundColor Red
    if (-not $DryRun) { Fail "Byte-preservation gate failed for ${what}: the merge changed the $label fields. Not publishing." }
}

# --- 1. Version (same read as build-app.ps1) --------------------------------------------
$licPy = Join-Path $here "live_transcribe\licensing.py"
if (-not (Test-Path $licPy)) { Fail "licensing.py not found; run from the project root." }
$verLine = Get-Content $licPy | Where-Object { $_ -match 'APP_VERSION\s*=' } | Select-Object -First 1
if ($verLine -notmatch '"([0-9]+\.[0-9]+\.[0-9]+)"') { Fail "Could not read APP_VERSION from licensing.py." }
$ver = $Matches[1]
Write-Host ""
Write-Host "  Releasing Volksmond $ver" -ForegroundColor Cyan

# ===========================  MAC LANE (-MacDmg)  =========================================
# Self-contained on purpose: it exits before the Windows lane below, so a mac publish can
# never touch the Windows objects (and vice versa). See docs/mac-port-plan.md WP-G.
if ($NotarisationJson -and -not $MacDmg) { Fail "-NotarisationJson only makes sense with -MacDmg." }
if ($MacDmg) {
    if ($Build -or $TrustOnly -or $VirusTotal -or $VtUrl) {
        Fail "-MacDmg is its own lane and cannot be combined with -Build, -TrustOnly, -VirusTotal or -VtUrl."
    }
    Write-Host "  Mac lane:  publishing the macOS DMG" -ForegroundColor Cyan

    # --- M1. The DMG (built, signed, notarised and stapled by CI; WP-F) -------------------
    if (-not (Test-Path $MacDmg)) { Fail "DMG not found: $MacDmg" }
    if ([System.IO.Path]::GetExtension($MacDmg).ToLower() -ne ".dmg") { Fail "-MacDmg expects a .dmg file, got: $MacDmg" }
    $dmg = (Resolve-Path $MacDmg).Path
    $dmgMb = [math]::Round((Get-Item $dmg).Length / 1MB, 1)
    Write-Host "  DMG:       $dmg  ($dmgMb MB)" -ForegroundColor Green

    # --- M2. SHA-256 of the exact bytes to be published ------------------------------------
    $dmgSha = (Get-FileHash -Path $dmg -Algorithm SHA256).Hash   # UPPERCASE hex, same as Windows
    Write-Host "  SHA-256:   $dmgSha" -ForegroundColor Green

    # --- M3. Mandatory local Defender scan of the DMG --------------------------------------
    # Apple notarisation is the primary malware attestation for the mac artifact; this is the
    # belt-and-braces second scan, zero cost because publishing happens from this machine.
    $macDefender = Invoke-DefenderScan $dmg "the DMG"

    # --- M4. Notarisation sidecar (CI emits it next to the DMG) ----------------------------
    # The sidecar must describe THIS release and THIS artifact: CI writes the APP_VERSION it
    # built and the SHA-256 of the DMG it notarised into the sidecar, and we verify both
    # against what this run sees. A mismatch means the wrong artifact pair was downloaded
    # (publishing would ship a DMG under a version/attestation Apple never saw together).
    # Mismatches and a non-Accepted status hard-fail a publish; -DryRun warns and continues
    # so the lane stays testable end to end.
    $notarisation = $null
    if ($NotarisationJson) {
        if (-not (Test-Path $NotarisationJson)) { Fail "Notarisation sidecar not found: $NotarisationJson" }
        try { $n = Get-Content $NotarisationJson -Raw | ConvertFrom-Json }
        catch { Fail "Notarisation sidecar is not valid JSON: $NotarisationJson" }
        foreach ($k in @("submission_id", "status", "date", "version", "sha256")) {
            if (-not $n.$k) { Fail "Notarisation sidecar is missing '$k' (expected {submission_id, status, date, version, sha256}): $NotarisationJson" }
        }
        function Sidecar-Problem($msg) {
            if ($DryRun) { Write-Host "  WARNING: $msg (-DryRun continues; a real publish fails here)" -ForegroundColor Yellow }
            else { Fail $msg }
        }
        if ("$($n.version)" -ne $ver) {
            Sidecar-Problem "Notarisation sidecar version '$($n.version)' does not match APP_VERSION '$ver' (licensing.py). Wrong sidecar, wrong DMG, or wrong checkout."
        }
        if ("$($n.sha256)".ToUpper() -ne $dmgSha) {
            Sidecar-Problem "Notarisation sidecar sha256 '$("$($n.sha256)".ToUpper())' does not match the DMG bytes ($dmgSha). This sidecar was not produced for this DMG."
        }
        if ("$($n.status)" -ne "Accepted") {
            Sidecar-Problem "Notarisation status is '$($n.status)', expected 'Accepted'. Do not publish a DMG Apple rejected."
        }
        $notarisation = [ordered]@{
            submission_id = "$($n.submission_id)"
            status        = "$($n.status)"
            date          = "$($n.date)"
        }
        Write-Host "  Notarised: $($notarisation.submission_id) ($($notarisation.status), $($notarisation.date))" -ForegroundColor Green
    } elseif ($DryRun) {
        Write-Host "  Notarisation: no sidecar; allowed only because this is -DryRun (the mac trust entry gets no notarisation object this run)." -ForegroundColor Yellow
    } else {
        Fail "-MacDmg needs -NotarisationJson <path> (the CI sidecar, JSON {submission_id, status, date, version, sha256}). A mac release never publishes without the notarisation record; only -DryRun may omit it."
    }

    # --- M5. Merge the "mac" keys into the LIVE manifests ----------------------------------
    # The merge base is what is actually published (Get-MergeBaseRaw): read the live manifest,
    # add the one key, write. Every existing (Windows) field passes through in its original
    # order with its original value; the local site\ files become the working copy of the
    # merged result. The Windows lane never writes the "mac" key, so the two lanes ship
    # independently.
    $siteDir      = Join-Path $here "site"
    if (-not (Test-Path $siteDir)) { New-Item -ItemType Directory -Path $siteDir | Out-Null }
    $manifestPath = Join-Path $siteDir "latest.json"
    $trustPath    = Join-Path $siteDir "trust.json"

    $beforeLatest = Get-MergeBaseRaw "latest.json" $manifestPath
    $beforeTrust  = Get-MergeBaseRaw "trust.json"  $trustPath

    function Merge-MacKey($baseRaw, $macObj) {
        $cur = $baseRaw | ConvertFrom-Json
        $merged = [ordered]@{}
        foreach ($p in $cur.PSObject.Properties) { if ($p.Name -ne "mac") { $merged[$p.Name] = $p.Value } }
        $merged["mac"] = $macObj
        return $merged
    }

    if (-not $PSBoundParameters.ContainsKey('Notes')) {
        $Notes = ""
        try { $Notes = "$(($beforeLatest | ConvertFrom-Json).mac.notes)" } catch { $Notes = "" }
    }

    # latest.json mac entry: same shape as the top-level Windows fields. The url stays on the
    # allowlisted volksmond.digiphyte.com host (see the -SiteUrl comment above); the
    # volksmond.com repoint is a separate backlog item.
    $macLatest = [ordered]@{ version = $ver; url = $SiteUrl; notes = $Notes }
    # trust.json mac entry: version, filename, sha256 (UPPERCASE hex), published, then
    # notarisation (the Apple attestation) and defender (the local rescan). The Windows
    # top-level field names above this key are a contract with trust.html; never touched here.
    $macTrust = [ordered]@{
        version  = $ver
        filename = "Volksmond-$ver.dmg"
        sha256   = $dmgSha
        published = (Get-Date -Format 'yyyy-MM-dd')
    }
    if ($notarisation) { $macTrust["notarisation"] = $notarisation }
    $macTrust["defender"] = $macDefender

    Write-JsonNoBom $manifestPath (Merge-MacKey $beforeLatest $macLatest)
    Write-JsonNoBom $trustPath    (Merge-MacKey $beforeTrust  $macTrust)
    Write-Host "  Wrote:     site\latest.json + site\trust.json (mac version=$ver merged into the live manifests)" -ForegroundColor Green

    # Byte-preservation gate (publish-time, not just a dry-run display): the merge must not
    # have changed a single Windows field of either manifest. Fails the release on a publish.
    Write-Host "  Byte-preservation gate (manifest minus its 'mac' key, merge base vs merged):" -ForegroundColor Cyan
    Assert-OtherPlatformPreserved $beforeLatest (Get-Content $manifestPath -Raw) "latest.json" $false
    Assert-OtherPlatformPreserved $beforeTrust  (Get-Content $trustPath -Raw)    "trust.json"  $false

    # --- M6. Upload to R2 (same bucket, same doppler/wrangler pattern as the Windows lane) --
    if ($DryRun) {
        Write-Host ""
        Write-Host "  -DryRun: skipping the R2 uploads (Defender already ran locally). Would upload, in order:" -ForegroundColor Yellow
        @("Volksmond-$ver.dmg", "latest.json", "trust.json", "Volksmond-latest.dmg") |
            ForEach-Object { Write-Host "    $Bucket/$_" -ForegroundColor Yellow }
    } else {
        $wBase = Get-WranglerBase
        if (-not $wBase) { Fail "wrangler not found (checked PATH and npx). Install it with: npm i -g wrangler" }
        $doppler = (Get-Command doppler -ErrorAction SilentlyContinue).Source
        if (-not $doppler) { Fail "Doppler CLI not found. R2 uploads inject the Cloudflare R2 credentials via 'doppler run -p infra-ops -c prd'. Install Doppler and log in." }
        Write-Host "  Uploading via doppler run -p infra-ops -c prd -- $($wBase -join ' ')" -ForegroundColor DarkGray

        $savedToken = $env:DOPPLER_TOKEN
        if ($null -ne $savedToken) { Remove-Item Env:DOPPLER_TOKEN }
        try {
            function Put-R2Mac($key, $file, $ct, $cc) {
                Write-Host "  Uploading $key ..." -ForegroundColor Gray
                $j = @(); if ($Jurisdiction) { $j = @("--jurisdiction", $Jurisdiction) }
                $wr = @("r2", "object", "put", "$Bucket/$key", "--file", $file, "--remote", "--content-type", $ct, "--cache-control", $cc) + $j
                $dArgs = @("run", "-p", "infra-ops", "-c", "prd", "--") + $wBase + $wr
                & $doppler @dArgs
                if ($LASTEXITCODE -ne 0) { Fail "Upload of $key failed (rc=$LASTEXITCODE). Check bucket '$Bucket', jurisdiction '$Jurisdiction', and that Doppler infra-ops/prd holds the Cloudflare R2 credentials." }
            }
            # Order matters: the versioned DMG goes up FIRST (nothing public references it
            # yet), then the manifests that point at it, and the mutable Volksmond-latest.dmg
            # alias LAST, so at no point does anything public reference bytes that are not up
            # yet. Versioned DMG: immutable, cached forever. Alias + manifests: short cache.
            Put-R2Mac "Volksmond-$ver.dmg"    $dmg          "application/octet-stream" "public, max-age=31536000, immutable"
            Put-R2Mac "latest.json"           $manifestPath "application/json"         "public, max-age=300"
            Put-R2Mac "trust.json"            $trustPath    "application/json"         "public, max-age=300"
            Put-R2Mac "Volksmond-latest.dmg"  $dmg          "application/octet-stream" "public, max-age=300"
            Write-Host "  Uploaded 4 objects to $Bucket." -ForegroundColor Green
        } finally {
            if ($null -ne $savedToken) { $env:DOPPLER_TOKEN = $savedToken }
        }
    }

    # --- M7. Verify + summary ---------------------------------------------------------------
    if (-not $DryRun) {
        try {
            $live = (Invoke-WebRequest "https://$Domain/latest.json" -UseBasicParsing -TimeoutSec 20).Content | ConvertFrom-Json
            if ($live.mac.version -eq $ver) { Write-Host "  Verified:  https://$Domain/latest.json mac entry reports $ver" -ForegroundColor Green }
            else { Write-Host "  WARNING: https://$Domain/latest.json mac entry reports '$($live.mac.version)', expected '$ver'. Check the cache." -ForegroundColor Yellow }
        } catch {
            Write-Host "  WARNING: could not fetch https://$Domain/latest.json yet ($($_.Exception.Message))." -ForegroundColor Yellow
        }
    }

    Write-Host ""
    if ($DryRun) { Write-Host "  ===============  MAC DRY RUN $ver COMPLETE  ===============" -ForegroundColor Cyan }
    else { Write-Host "  ===============  MAC RELEASE $ver PUBLISHED  ===============" -ForegroundColor Cyan }
    Write-Host "  Download (versioned) : https://$Domain/Volksmond-$ver.dmg"
    Write-Host "  Download (stable)    : https://$Domain/Volksmond-latest.dmg"
    Write-Host "  SHA-256              : $dmgSha"
    Write-Host "  Defender             : $($macDefender.result) (engine $($macDefender.engine), definitions $($macDefender.definitions))"
    if ($notarisation) { Write-Host "  Notarisation         : $($notarisation.status) ($($notarisation.submission_id), $($notarisation.date))" }
    Write-Host "  Manifest / trust     : https://$Domain/latest.json  |  https://$Domain/trust.json"
    Write-Host ""
    exit 0
}

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

# --- 4. Malware scan: Microsoft Defender (mandatory, fully local, runs even in -DryRun) ---
$defender = Invoke-DefenderScan $exe

# --- 4b. VirusTotal (OPT-IN only) ----------------------------------------------------------
$vtLink = $null; $vtDet = $null
if ($VtUrl) {
    $vtLink = $VtUrl
    Write-Host "  VirusTotal: recording the supplied report link (no API call)." -ForegroundColor Gray
} elseif ($VirusTotal) {
    if ($DryRun) {
        Write-Host "  VirusTotal: -DryRun; skipping the opt-in VT scan (no network)." -ForegroundColor Yellow
    } else {
        $vtKey = Resolve-VtKey
        if (-not $vtKey) { Fail "-VirusTotal needs an API key: set `$env:VT_API_KEY or add it to Doppler (doppler secrets set VT_API_KEY -p infra-ops -c prd). It must be a licence appropriate to commercial use; the free API is ruled out. Or drop -VirusTotal (Defender is the mandatory scan) or pass -VtUrl <link>." }
        $vt = Invoke-VirusTotal $exe $shaLower $vtKey
        if ($vt) { $vtLink = $vt.url; $vtDet = $vt.detections }
    }
}

# --- 5. Write site/latest.json + site/trust.json (no BOM) --------------------------------
# The merge base is the LIVE manifests (Get-MergeBaseRaw): this lane rewrites the top-level
# Windows fields and must carry the "mac" key through from what is actually published, or a
# Windows release from a machine with stale local site\ files would silently drop the last
# mac release. The local site\ files become the working copy of the merged result.
$siteDir      = Join-Path $here "site"
if (-not (Test-Path $siteDir)) { New-Item -ItemType Directory -Path $siteDir | Out-Null }
$manifestPath = Join-Path $siteDir "latest.json"
$trustPath    = Join-Path $siteDir "trust.json"
$modelsPath   = Join-Path $siteDir "models.json"
if (-not (Test-Path $modelsPath)) { Fail "site/models.json not found; expected next to latest.json." }

$baseLatestRaw = Get-MergeBaseRaw "latest.json" $manifestPath
$baseTrustRaw  = Get-MergeBaseRaw "trust.json"  $trustPath

if (-not $PSBoundParameters.ContainsKey('Notes')) {
    try { $Notes = ($baseLatestRaw | ConvertFrom-Json).notes } catch { $Notes = "" }
}
$downloadUrl = "https://$Domain/Volksmond-Setup-$ver.exe"   # versioned, matches the hash
$latestAlias = "https://$Domain/Volksmond-Setup-latest.exe" # stable, for the site DOWNLOAD_URL

# Carry over the LIVE "mac" entry unchanged: the mac lane (-MacDmg) owns that key, and a
# Windows release must not drop or alter it (the two lanes ship independently). When no mac
# key exists the output is byte-identical to the pre-mac-lane format.
$prevMacLatest = $null; $prevMacTrust = $null
try { $prevMacLatest = ($baseLatestRaw | ConvertFrom-Json).mac } catch { }
try { $prevMacTrust  = ($baseTrustRaw  | ConvertFrom-Json).mac } catch { }

$latestObj = [ordered]@{ version = $ver; url = $SiteUrl; notes = $Notes }
if ($prevMacLatest) { $latestObj["mac"] = $prevMacLatest }
Write-JsonNoBom $manifestPath $latestObj
# trust.json: contract shared with trust.html. REQUIRED: version, filename, sha256 (UPPERCASE
# hex), published. OPTIONAL: virustotal (lowercase-hash GUI permalink, only when a link was
# supplied or a VT scan ran) and defender (the local scan record). Do not rename fields.
# OPTIONAL "mac" key (owned by the -MacDmg lane, passed through here): {version, filename,
# sha256, published, notarisation{submission_id, status, date}, defender{...}}.
$trust = [ordered]@{
    version  = $ver
    filename = "Volksmond-Setup-$ver.exe"
    sha256   = $shaUpper
}
if ($vtLink) { $trust["virustotal"] = $vtLink }
$trust["published"] = (Get-Date -Format 'yyyy-MM-dd')
if ($defender) { $trust["defender"] = $defender }
if ($prevMacTrust) { $trust["mac"] = $prevMacTrust }
Write-JsonNoBom $trustPath $trust
Write-Host "  Wrote:     site\latest.json + site\trust.json (version=$ver; mac key carried through from the merge base)" -ForegroundColor Green

# Byte-preservation gate (publish-time): this lane owns every top-level field, so the only
# thing it must preserve byte-for-byte is the other platform's "mac" key. Fails a publish.
Write-Host "  Byte-preservation gate (manifest's 'mac' key only, merge base vs merged):" -ForegroundColor Cyan
Assert-OtherPlatformPreserved $baseLatestRaw (Get-Content $manifestPath -Raw) "latest.json" $true
Assert-OtherPlatformPreserved $baseTrustRaw  (Get-Content $trustPath -Raw)    "trust.json"  $true

# --- 6. Upload to R2 ---------------------------------------------------------------------
if ($DryRun) {
    Write-Host ""
    Write-Host "  -DryRun: skipping the R2 uploads (Defender already ran locally). Would upload:" -ForegroundColor Yellow
    $would = @("Volksmond-Setup-$ver.exe", "Volksmond-Setup-latest.exe", "latest.json", "models.json", "trust.json")
    if ($TrustOnly) { $would = @("trust.json") }
    $would | ForEach-Object { Write-Host "    $Bucket/$_" -ForegroundColor Yellow }
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
        if (-not $TrustOnly) {
            Put-R2 "Volksmond-Setup-$ver.exe"   $exe          "application/octet-stream" "public, max-age=31536000, immutable"
            Put-R2 "Volksmond-Setup-latest.exe" $exe          "application/octet-stream" "public, max-age=300"
            Put-R2 "latest.json"                $manifestPath "application/json"         "public, max-age=300"
            Put-R2 "models.json"                $modelsPath   "application/json"         "public, max-age=300"
        }
        Put-R2 "trust.json"                 $trustPath    "application/json"         "public, max-age=300"
        if ($TrustOnly) { Write-Host "  -TrustOnly: uploaded trust.json only." -ForegroundColor Green }
        else { Write-Host "  Uploaded 5 objects to $Bucket." -ForegroundColor Green }
    } finally {
        if ($null -ne $savedToken) { $env:DOPPLER_TOKEN = $savedToken }
    }
}

# --- 7. Verify + summary -----------------------------------------------------------------
# -TrustOnly only re-published trust.json, so verify that instead (same version field).
$manifestUrl = "https://$Domain/latest.json"
if ($TrustOnly) { $manifestUrl = "https://$Domain/trust.json" }
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
if ($TrustOnly) { Write-Host "  ===============  TRUST.JSON RE-PUBLISHED FOR $ver  ===============" -ForegroundColor Cyan }
else { Write-Host "  ===============  RELEASE $ver PUBLISHED  ===============" -ForegroundColor Cyan }
Write-Host "  Download (versioned) : $downloadUrl"
Write-Host "  Download (stable)    : $latestAlias"
Write-Host "  SHA-256              : $shaUpper"
Write-Host "  Defender             : $($defender.result) (engine $($defender.engine), definitions $($defender.definitions))"
if ($vtLink) {
    Write-Host "  VirusTotal           : $vtLink"
    if ($vtDet) { Write-Host "  VT detections        : $vtDet" }
}
Write-Host "  Manifest / trust     : $manifestUrl  |  https://$Domain/trust.json"
Write-Host ""
Write-Host "  trust.html reads trust.json, and the site's DOWNLOAD_URL is the stable alias, so"
Write-Host "  the marketing site needs NO per-release change. (One-time site wiring: RELEASE.md.)" -ForegroundColor Gray
Write-Host ""
