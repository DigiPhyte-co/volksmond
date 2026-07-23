# test_release_ps1.ps1 - offline test harness for release.ps1 (WP-L4).
#
# Covers the N-platform byte-preservation gate (Get-ProjectedJson /
# Assert-OtherPlatformsPreserved / Merge-PlatformKey), the -LinuxDeb lane, and regression
# of the -MacDmg and Windows lanes, WITHOUT any network write:
#   - pure-function tests run against function bodies extracted from release.ps1 via the
#     PowerShell AST (release.ps1 is a top-to-bottom script, so it is never dot-sourced);
#   - lane tests copy release.ps1 into a throwaway sandbox (temp dir with its own site\
#     fixtures, dummy artifacts and licensing.py) and run it with -DryRun and
#     -Domain release-test.invalid, so the live-manifest fetch fails fast and the lane
#     falls back to the LOCAL fixture manifests (the documented -DryRun behaviour). The
#     real site\ directory and the live host are never touched. The mandatory Defender
#     scan DOES run (on tiny dummy files; it is part of the lane under test).
#   - equivalence tests extract the OLD (pre-generalisation) gate from git at
#     $BaselineCommit and byte-diff projections and full lane outputs old vs new, proving
#     the two-platform behaviour survived the refactor. Skipped if git history is absent.
#
# Run:  powershell -NoProfile -ExecutionPolicy Bypass -File tests\test_release_ps1.ps1
# Exit: 0 = all checks passed, 1 = failures (count printed either way).

param(
    [string]$BaselineCommit = "bbd39d7"
)

$here = Split-Path -Parent $PSScriptRoot   # repo root (this file lives in tests\)
$releasePs1 = Join-Path $here "release.ps1"
$licPy = Join-Path $here "live_transcribe\licensing.py"
$testDomain = "release-test.invalid"       # RFC 2606: never resolves, fetch fails fast

$script:pass = 0; $script:fail = 0; $script:failures = @()
function Check($name, $cond) {
    if ($cond) { $script:pass++; Write-Host "  ok   $name" -ForegroundColor Green }
    else { $script:fail++; $script:failures += $name; Write-Host "  FAIL $name" -ForegroundColor Red }
}

# --- version under test (same regex as release.ps1 / build-app.ps1) -----------------------
$verLine = Get-Content $licPy | Where-Object { $_ -match 'APP_VERSION\s*=' } | Select-Object -First 1
if ($verLine -notmatch '"([0-9]+\.[0-9]+\.[0-9]+)"') { Write-Host "cannot read APP_VERSION"; exit 1 }
$ver = $Matches[1]
Write-Host ""
Write-Host "release.ps1 harness (APP_VERSION $ver, baseline $BaselineCommit)" -ForegroundColor Cyan

# ==== A. static ============================================================================
Write-Host "`n[A] static" -ForegroundColor Cyan
$errs = $null; $tok = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($releasePs1, [ref]$tok, [ref]$errs)
Check "A1 release.ps1 parses with zero errors" ($errs.Count -eq 0)
$src = Get-Content $releasePs1 -Raw
Check "A2 platform key list is @(mac, linux)" ($src -match '\$PlatformKeys\s*=\s*@\("mac",\s*"linux"\)')

# ==== B. pure functions (extracted from the AST) ===========================================
Write-Host "`n[B] gate functions" -ForegroundColor Cyan
function Get-FunctionText($astRoot, $name) {
    $fn = $astRoot.FindAll({ param($a)
        $a -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $a.Name -eq $name }, $true) |
        Select-Object -First 1
    if (-not $fn) { throw "function $name not found" }
    return $fn.Extent.Text
}
# scope the extracted functions need: the platform list, a throwing Fail, publish mode.
$PlatformKeys = @("mac", "linux")
function Fail($msg) { throw "RELEASEFAIL: $msg" }
$DryRun = $false
. ([scriptblock]::Create((Get-FunctionText $ast "Get-ProjectedJson")))
. ([scriptblock]::Create((Get-FunctionText $ast "Assert-OtherPlatformsPreserved")))
. ([scriptblock]::Create((Get-FunctionText $ast "Merge-PlatformKey")))

# the OLD (two-platform) Get-ProjectedJson from git, renamed, for equivalence checks
$oldAvailable = $false
try {
    $oldSrcLines = git -C $here show "${BaselineCommit}:release.ps1" 2>$null
    if ($LASTEXITCODE -eq 0 -and $oldSrcLines) {
        $oldSrc = ($oldSrcLines -join "`n")
        $e2 = $null; $t2 = $null
        $oldAst = [System.Management.Automation.Language.Parser]::ParseInput($oldSrc, [ref]$t2, [ref]$e2)
        $oldText = (Get-FunctionText $oldAst "Get-ProjectedJson") -replace 'Get-ProjectedJson', 'Get-ProjectedJsonOld'
        . ([scriptblock]::Create($oldText))
        $oldAvailable = $true
    }
} catch { }

# fixtures
$fxWinOnly = '{ "version": "1.9.0", "url": "https://volksmond.digiphyte.com/", "notes": "win notes" }'
$fxWinMac = '{ "version": "1.9.0", "url": "https://volksmond.digiphyte.com/", "notes": "win notes",
  "mac": { "version": "1.8.0", "url": "https://volksmond.digiphyte.com/", "notes": "mac notes" } }'
$fxTri = '{ "version": "1.9.0", "url": "https://volksmond.digiphyte.com/", "notes": "win notes",
  "mac": { "version": "1.8.0", "url": "https://volksmond.digiphyte.com/", "notes": "mac notes" },
  "linux": { "version": "1.7.0", "url": "https://dl.volksmond.com/Volksmond-1.7.0.deb", "notes": "linux notes" } }'

if ($oldAvailable) {
    Check "B1 old(keep mac) == new('mac') on windows-only fixture" ((Get-ProjectedJsonOld $fxWinOnly $true) -eq (Get-ProjectedJson $fxWinOnly "mac"))
    Check "B2 old(drop mac) == new('windows') on windows-only fixture" ((Get-ProjectedJsonOld $fxWinOnly $false) -eq (Get-ProjectedJson $fxWinOnly "windows"))
    Check "B3 old(keep mac) == new('mac') on windows+mac fixture" ((Get-ProjectedJsonOld $fxWinMac $true) -eq (Get-ProjectedJson $fxWinMac "mac"))
    Check "B4 old(drop mac) == new('windows') on windows+mac fixture" ((Get-ProjectedJsonOld $fxWinMac $false) -eq (Get-ProjectedJson $fxWinMac "windows"))
} else {
    Write-Host "  SKIP B1-B4 (baseline commit $BaselineCommit not readable from git)" -ForegroundColor Yellow
}
$pLinux = Get-ProjectedJson $fxTri "linux" | ConvertFrom-Json
$pWin = Get-ProjectedJson $fxTri "windows" | ConvertFrom-Json
$pMac = Get-ProjectedJson $fxTri "mac" | ConvertFrom-Json
Check "B5 'linux' projection keeps only the linux key" (($pLinux.PSObject.Properties.Name -join ",") -eq "linux")
Check "B6 'windows' projection excludes mac and linux" (($pWin.PSObject.Properties.Name -join ",") -eq "version,url,notes")
Check "B7 'mac' projection keeps only the mac key" (($pMac.PSObject.Properties.Name -join ",") -eq "mac")

$newLinux = [ordered]@{ version = "2.0.0"; url = "https://dl.volksmond.com/Volksmond-2.0.0.deb"; notes = "new" }
$mergedTri = (Merge-PlatformKey $fxTri "linux" $newLinux) | ConvertTo-Json -Depth 10
Check "B8 merge(linux): Windows projection byte-identical" ((Get-ProjectedJson $fxTri "windows") -eq (Get-ProjectedJson $mergedTri "windows"))
Check "B9 merge(linux): mac projection byte-identical" ((Get-ProjectedJson $fxTri "mac") -eq (Get-ProjectedJson $mergedTri "mac"))
Check "B10 merge(linux): linux entry replaced" ((($mergedTri | ConvertFrom-Json).linux.version) -eq "2.0.0")
Check "B11 merge(linux): top-level property order preserved" (((($mergedTri | ConvertFrom-Json).PSObject.Properties.Name) -join ",") -eq "version,url,notes,mac,linux")

function Gate-Throws($beforeRaw, $afterRaw, $publishing) {
    try { Assert-OtherPlatformsPreserved $beforeRaw $afterRaw "test.json" $publishing 6>$null; return $false }
    catch { return $true }
}
$corruptWin = $fxTri -replace '"win notes"', '"tampered"'
$corruptMac = $fxTri -replace '"mac notes"', '"tampered"'
$corruptLinux = $fxTri -replace '"linux notes"', '"tampered"'
$droppedLinux = $fxWinMac  # tri fixture minus its linux key entirely
Check "B12 gate passes: clean linux merge (publish mode)" (-not (Gate-Throws $fxTri $mergedTri "linux"))
Check "B13 gate fails: Windows field changed while publishing linux" (Gate-Throws $fxTri $corruptWin "linux")
Check "B14 gate fails: mac field changed while publishing linux" (Gate-Throws $fxTri $corruptMac "linux")
Check "B15 gate fails: linux field changed while publishing mac" (Gate-Throws $fxTri $corruptLinux "mac")
Check "B16 gate passes: windows publish with mac+linux intact" (-not (Gate-Throws $fxTri $corruptWin "windows"))
Check "B17 gate fails: linux key dropped while publishing windows" (Gate-Throws $fxTri $droppedLinux "windows")

# ==== C. lanes end to end (sandboxed -DryRun) ==============================================
Write-Host "`n[C] lanes (sandboxed, -Domain $testDomain, no uploads)" -ForegroundColor Cyan
$utf8 = New-Object System.Text.UTF8Encoding($false)
$sbRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("vm-release-tests-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $sbRoot | Out-Null

function New-Sandbox($name, $scriptPath, $latestJson, $trustJson) {
    $sb = Join-Path $sbRoot $name
    New-Item -ItemType Directory -Path $sb | Out-Null
    Copy-Item $scriptPath (Join-Path $sb "release.ps1")
    New-Item -ItemType Directory -Path (Join-Path $sb "live_transcribe") | Out-Null
    Copy-Item $licPy (Join-Path $sb "live_transcribe\licensing.py")
    $site = Join-Path $sb "site"
    New-Item -ItemType Directory -Path $site | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $site "latest.json"), $latestJson, $utf8)
    [System.IO.File]::WriteAllText((Join-Path $site "trust.json"), $trustJson, $utf8)
    [System.IO.File]::WriteAllText((Join-Path $site "models.json"), '{ "models": [] }', $utf8)
    return $sb
}
function Invoke-Lane($sb, [string[]]$laneArgs) {
    $allArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $sb "release.ps1")) + $laneArgs + @("-Domain", $testDomain)
    $out = & powershell.exe @allArgs 2>&1 | ForEach-Object { "$_" }
    return [pscustomobject]@{ Out = ($out -join "`n"); Rc = $LASTEXITCODE }
}
function Get-UploadList($out) {
    @(($out -split "`n") | Where-Object { $_ -match '^\s+volksmond/' } | ForEach-Object { ($_ -replace '^\s+volksmond/', '').Trim() })
}

# realistic three-platform fixtures for the new-script lane tests
$fxLatest3 = @"
{ "version": "1.9.0", "url": "https://volksmond.digiphyte.com/", "notes": "win notes",
  "mac": { "version": "1.8.0", "url": "https://volksmond.digiphyte.com/", "notes": "mac notes" },
  "linux": { "version": "1.7.0", "url": "https://dl.volksmond.com/Volksmond-1.7.0.deb", "notes": "linux notes" } }
"@
$fxTrust3 = @"
{ "version": "1.9.0", "filename": "Volksmond-Setup-1.9.0.exe", "sha256": "AB12", "published": "2026-07-01",
  "defender": { "result": "clean", "engine": "1.1.0.0", "definitions": "1.0.0.0", "scanned": "2026-07-01" },
  "mac": { "version": "1.8.0", "filename": "Volksmond-1.8.0.dmg", "sha256": "CD34", "published": "2026-06-01",
    "notarisation": { "submission_id": "x", "status": "Accepted", "date": "2026-06-01" },
    "defender": { "result": "clean", "engine": "1.1.0.0", "definitions": "1.0.0.0", "scanned": "2026-06-01" } },
  "linux": { "version": "1.7.0", "filename": "Volksmond-1.7.0.deb", "sha256": "EF56", "published": "2026-05-01",
    "defender": { "result": "clean", "engine": "1.1.0.0", "definitions": "1.0.0.0", "scanned": "2026-05-01" } } }
"@

# --- C1-C7: -LinuxDeb parameter guards (no -DryRun: each must fail BEFORE scan/fetch/upload)
$sbGuard = New-Sandbox "guards" $releasePs1 $fxLatest3 $fxTrust3
$deb = Join-Path $sbGuard "Volksmond-$ver.deb"
[System.IO.File]::WriteAllText($deb, "dummy deb bytes", $utf8)
$dmg = Join-Path $sbGuard "Volksmond-$ver.dmg"
[System.IO.File]::WriteAllText($dmg, "dummy dmg bytes", $utf8)
$r = Invoke-Lane $sbGuard @("-LinuxDeb", $deb, "-MacDmg", $dmg)
Check "C1 -LinuxDeb + -MacDmg rejected" ($r.Rc -eq 1 -and $r.Out -match "separate lanes")
$r = Invoke-Lane $sbGuard @("-LinuxDeb", $deb, "-Build")
Check "C2 -LinuxDeb + -Build rejected" ($r.Rc -eq 1 -and $r.Out -match "its own lane")
$r = Invoke-Lane $sbGuard @("-LinuxDeb", $deb, "-VtUrl", "https://example.com/x")
Check "C3 -LinuxDeb + -VtUrl rejected" ($r.Rc -eq 1 -and $r.Out -match "its own lane")
$r = Invoke-Lane $sbGuard @("-LinuxDeb", $deb, "-NotarisationJson", $dmg)
Check "C4 -LinuxDeb + -NotarisationJson rejected" ($r.Rc -eq 1 -and $r.Out -match "NotarisationJson")
$r = Invoke-Lane $sbGuard @("-LinuxDeb", (Join-Path $sbGuard "missing.deb"))
Check "C5 missing .deb rejected" ($r.Rc -eq 1 -and $r.Out -match "not found")
$notDeb = Join-Path $sbGuard "Volksmond-$ver.txt"
[System.IO.File]::WriteAllText($notDeb, "x", $utf8)
$r = Invoke-Lane $sbGuard @("-LinuxDeb", $notDeb)
Check "C6 non-.deb extension rejected" ($r.Rc -eq 1 -and $r.Out -match "expects a .deb")
$wrongVer = Join-Path $sbGuard "Volksmond-9.9.9.deb"
[System.IO.File]::WriteAllText($wrongVer, "x", $utf8)
$r = Invoke-Lane $sbGuard @("-LinuxDeb", $wrongVer)
Check "C7 filename version mismatch fails a publish" ($r.Rc -eq 1 -and $r.Out -match "does not match APP_VERSION")

# --- C8-C14: -LinuxDeb -DryRun happy path (no tarball) -------------------------------------
$sbLin = New-Sandbox "linux" $releasePs1 $fxLatest3 $fxTrust3
$deb = Join-Path $sbLin "Volksmond-$ver.deb"
[System.IO.File]::WriteAllText($deb, "dummy deb bytes", $utf8)
$r = Invoke-Lane $sbLin @("-LinuxDeb", $deb, "-DryRun")
Check "C8 -LinuxDeb -DryRun exits 0" ($r.Rc -eq 0)
$ul = Get-UploadList $r.Out
Check "C9 upload order: versioned .deb, manifests, latest alias LAST" (($ul -join "|") -eq "Volksmond-$ver.deb|latest.json|trust.json|Volksmond-latest.deb")
$mergedLatest = Get-Content (Join-Path $sbLin "site\latest.json") -Raw
$mergedTrust = Get-Content (Join-Path $sbLin "site\trust.json") -Raw
$ml = $mergedLatest | ConvertFrom-Json; $mt = $mergedTrust | ConvertFrom-Json
Check "C10 latest.json linux entry: version + url point at the versioned .deb" (
    $ml.linux.version -eq $ver -and $ml.linux.url -eq "https://$testDomain/Volksmond-$ver.deb")
$expSha = (Get-FileHash -Path $deb -Algorithm SHA256).Hash
Check "C11 trust.json linux entry: filename/sha256(UPPER)/published/defender" (
    $mt.linux.filename -eq "Volksmond-$ver.deb" -and
    $mt.linux.sha256 -ceq $expSha -and
    $mt.linux.published -eq (Get-Date -Format 'yyyy-MM-dd') -and
    $mt.linux.defender.result -eq "clean")
Check "C12 trust.json linux entry has NO notarisation field" (-not ($mt.linux.PSObject.Properties.Name -contains "notarisation"))
Check "C13 linux publish left Windows fields byte-identical (both manifests)" (
    ((Get-ProjectedJson $fxLatest3 "windows") -eq (Get-ProjectedJson $mergedLatest "windows")) -and
    ((Get-ProjectedJson $fxTrust3 "windows") -eq (Get-ProjectedJson $mergedTrust "windows")))
Check "C14 linux publish left mac fields byte-identical (both manifests)" (
    ((Get-ProjectedJson $fxLatest3 "mac") -eq (Get-ProjectedJson $mergedLatest "mac")) -and
    ((Get-ProjectedJson $fxTrust3 "mac") -eq (Get-ProjectedJson $mergedTrust "mac")))

# --- C15: tarball beside the .deb is published versioned-only, before the manifests --------
$sbTar = New-Sandbox "linux-tar" $releasePs1 $fxLatest3 $fxTrust3
$deb = Join-Path $sbTar "Volksmond-$ver.deb"
[System.IO.File]::WriteAllText($deb, "dummy deb bytes", $utf8)
[System.IO.File]::WriteAllText((Join-Path $sbTar "Volksmond-$ver-linux-x64.tar.gz"), "dummy tarball", $utf8)
$r = Invoke-Lane $sbTar @("-LinuxDeb", $deb, "-DryRun")
$ul = Get-UploadList $r.Out
Check "C15 tarball variant: deb, tarball, manifests, alias LAST" ($r.Rc -eq 0 -and
    (($ul -join "|") -eq "Volksmond-$ver.deb|Volksmond-$ver-linux-x64.tar.gz|latest.json|trust.json|Volksmond-latest.deb"))

# --- C16-C17: -MacDmg -DryRun regression on three-platform fixtures ------------------------
$sbMac = New-Sandbox "mac" $releasePs1 $fxLatest3 $fxTrust3
$dmg = Join-Path $sbMac "Volksmond-$ver.dmg"
[System.IO.File]::WriteAllText($dmg, "dummy dmg bytes", $utf8)
$r = Invoke-Lane $sbMac @("-MacDmg", $dmg, "-DryRun")
$ul = Get-UploadList $r.Out
Check "C16 -MacDmg -DryRun exits 0, upload order dmg/manifests/alias" ($r.Rc -eq 0 -and
    (($ul -join "|") -eq "Volksmond-$ver.dmg|latest.json|trust.json|Volksmond-latest.dmg"))
$mergedLatest = Get-Content (Join-Path $sbMac "site\latest.json") -Raw
$mergedTrust = Get-Content (Join-Path $sbMac "site\trust.json") -Raw
Check "C17 mac publish left Windows AND linux fields byte-identical" (
    ((Get-ProjectedJson $fxLatest3 "windows") -eq (Get-ProjectedJson $mergedLatest "windows")) -and
    ((Get-ProjectedJson $fxTrust3 "windows") -eq (Get-ProjectedJson $mergedTrust "windows")) -and
    ((Get-ProjectedJson $fxLatest3 "linux") -eq (Get-ProjectedJson $mergedLatest "linux")) -and
    ((Get-ProjectedJson $fxTrust3 "linux") -eq (Get-ProjectedJson $mergedTrust "linux")))

# --- C18-C19: Windows lane -DryRun regression: carries mac AND linux through ---------------
$sbWin = New-Sandbox "windows" $releasePs1 $fxLatest3 $fxTrust3
[System.IO.File]::WriteAllText((Join-Path $sbWin "Volksmond-Setup-$ver.exe"), "dummy exe bytes", $utf8)
$r = Invoke-Lane $sbWin @("-DryRun")
$ul = Get-UploadList $r.Out
Check "C18 Windows -DryRun exits 0, upload list unchanged (5 objects)" ($r.Rc -eq 0 -and
    (($ul -join "|") -eq "Volksmond-Setup-$ver.exe|Volksmond-Setup-latest.exe|latest.json|models.json|trust.json"))
$mergedLatest = Get-Content (Join-Path $sbWin "site\latest.json") -Raw
$mergedTrust = Get-Content (Join-Path $sbWin "site\trust.json") -Raw
$ml = $mergedLatest | ConvertFrom-Json
Check "C19 Windows publish: top-level version updated, mac + linux byte-identical" (
    $ml.version -eq $ver -and
    ((Get-ProjectedJson $fxLatest3 "mac") -eq (Get-ProjectedJson $mergedLatest "mac")) -and
    ((Get-ProjectedJson $fxLatest3 "linux") -eq (Get-ProjectedJson $mergedLatest "linux")) -and
    ((Get-ProjectedJson $fxTrust3 "mac") -eq (Get-ProjectedJson $mergedTrust "mac")) -and
    ((Get-ProjectedJson $fxTrust3 "linux") -eq (Get-ProjectedJson $mergedTrust "linux")))

# ==== D. old-vs-new behavioural baseline (two-platform fixtures, byte-identical outputs) ===
Write-Host "`n[D] old-vs-new equivalence (baseline $BaselineCommit)" -ForegroundColor Cyan
if ($oldAvailable) {
    $oldPs1 = Join-Path $sbRoot "old-release.ps1"
    [System.IO.File]::WriteAllLines($oldPs1, $oldSrcLines)
    # two-platform fixtures: no linux key, so old and new scripts must emit identical bytes
    $fxLatest2 = @"
{ "version": "1.9.0", "url": "https://volksmond.digiphyte.com/", "notes": "win notes",
  "mac": { "version": "1.8.0", "url": "https://volksmond.digiphyte.com/", "notes": "mac notes" } }
"@
    $fxTrust2 = @"
{ "version": "1.9.0", "filename": "Volksmond-Setup-1.9.0.exe", "sha256": "AB12", "published": "2026-07-01",
  "defender": { "result": "clean", "engine": "1.1.0.0", "definitions": "1.0.0.0", "scanned": "2026-07-01" },
  "mac": { "version": "1.8.0", "filename": "Volksmond-1.8.0.dmg", "sha256": "CD34", "published": "2026-06-01",
    "notarisation": { "submission_id": "x", "status": "Accepted", "date": "2026-06-01" },
    "defender": { "result": "clean", "engine": "1.1.0.0", "definitions": "1.0.0.0", "scanned": "2026-06-01" } } }
"@
    foreach ($lane in @("mac", "windows")) {
        $sbOld = New-Sandbox "old-$lane" $oldPs1 $fxLatest2 $fxTrust2
        $sbNew = New-Sandbox "new-$lane" $releasePs1 $fxLatest2 $fxTrust2
        foreach ($sb in @($sbOld, $sbNew)) {
            if ($lane -eq "mac") {
                [System.IO.File]::WriteAllText((Join-Path $sb "Volksmond-$ver.dmg"), "dummy dmg bytes", $utf8)
            } else {
                [System.IO.File]::WriteAllText((Join-Path $sb "Volksmond-Setup-$ver.exe"), "dummy exe bytes", $utf8)
            }
        }
        $laneArgs = if ($lane -eq "mac") { @("-MacDmg", (Join-Path $sbOld "Volksmond-$ver.dmg"), "-DryRun") } else { @("-DryRun") }
        $rOld = Invoke-Lane $sbOld $laneArgs
        $laneArgs = if ($lane -eq "mac") { @("-MacDmg", (Join-Path $sbNew "Volksmond-$ver.dmg"), "-DryRun") } else { @("-DryRun") }
        $rNew = Invoke-Lane $sbNew $laneArgs
        $okRc = ($rOld.Rc -eq 0 -and $rNew.Rc -eq 0)
        $sameLatest = ((Get-Content (Join-Path $sbOld "site\latest.json") -Raw) -ceq (Get-Content (Join-Path $sbNew "site\latest.json") -Raw))
        $sameTrust = ((Get-Content (Join-Path $sbOld "site\trust.json") -Raw) -ceq (Get-Content (Join-Path $sbNew "site\trust.json") -Raw))
        Check "D $lane lane: old and new scripts exit 0" $okRc
        Check "D $lane lane: latest.json byte-identical old vs new" $sameLatest
        Check "D $lane lane: trust.json byte-identical old vs new" $sameTrust
    }
} else {
    Write-Host "  SKIP section D (baseline commit $BaselineCommit not readable from git)" -ForegroundColor Yellow
}

# ==== summary ==============================================================================
Remove-Item -Recurse -Force $sbRoot -ErrorAction SilentlyContinue
$total = $script:pass + $script:fail
Write-Host ""
if ($script:fail -eq 0) {
    Write-Host "$($script:pass)/$total checks passed" -ForegroundColor Green
    exit 0
} else {
    Write-Host "$($script:pass)/$total checks passed; FAILURES:" -ForegroundColor Red
    $script:failures | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    exit 1
}
