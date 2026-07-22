[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')]
    [string]$Version,

    [Parameter()]
    [string]$OutputDirectory = 'dist',

    [Parameter()]
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$OutputRoot = if ([IO.Path]::IsPathRooted($OutputDirectory)) {
    [IO.Path]::GetFullPath($OutputDirectory)
} else {
    [IO.Path]::GetFullPath((Join-Path $ProjectRoot $OutputDirectory))
}
$BundleName = "RelinkSaveForge-win-x64-v$Version"
$Stage = Join-Path $OutputRoot $BundleName
$ZipPath = Join-Path $OutputRoot "$BundleName.zip"

if ((Test-Path -LiteralPath $Stage) -or (Test-Path -LiteralPath $ZipPath)) {
    if (-not $Force) {
        throw "Bundle output already exists. Use -Force to replace: $Stage or $ZipPath"
    }
    Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path $OutputRoot, $Stage | Out-Null

function Copy-ProjectFile {
    param(
        [Parameter(Mandatory)] [string]$SourceRelativePath,
        [Parameter()] [string]$DestinationRelativePath
    )
    if ([string]::IsNullOrWhiteSpace($DestinationRelativePath)) {
        $DestinationRelativePath = $SourceRelativePath
    }
    $NormalizedSource = $SourceRelativePath.Replace('/', '\')
    $NormalizedDestination = $DestinationRelativePath.Replace('/', '\')
    $Source = [IO.Path]::GetFullPath((Join-Path $ProjectRoot $NormalizedSource))
    if (-not $Source.StartsWith($ProjectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Runtime source path escapes the project root: $SourceRelativePath"
    }
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Runtime allowlist file is missing: $Source"
    }
    $Destination = [IO.Path]::GetFullPath((Join-Path $Stage $NormalizedDestination))
    if (-not $Destination.StartsWith($Stage + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Runtime destination path escapes the staging root: $DestinationRelativePath"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination
}

$AppSource = Join-Path $ProjectRoot 'app'
Copy-Item -LiteralPath $AppSource -Destination $Stage -Recurse
Copy-ProjectFile -SourceRelativePath 'packaging/bootstrap-runtime.ps1'
Copy-ProjectFile `
    -SourceRelativePath 'packaging/BUNDLE_README.md' `
    -DestinationRelativePath 'README.md'

$RuntimeFiles = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($RelativePath in @(
    'RelinkSaveForge.cmd',
    'LICENSE',
    'THIRD_PARTY_NOTICES.md',
    'docs/ONE_CLICK_WINDOWS.md',
    'docs/SAVE_SAFETY.md',
    'scripts/build_all_sigils_strict.py',
    'scripts/build_materials_complete.py',
    'scripts/equip_legacy_gold_sigils.py',
    'scripts/gbfr_hash.py',
    'scripts/save_editor_api.py'
)) {
    $Source = Join-Path $ProjectRoot $RelativePath
    if (Test-Path -LiteralPath $Source -PathType Leaf) {
        [void]$RuntimeFiles.Add($RelativePath)
    }
}

$PackSource = Join-Path $ProjectRoot 'presets\packs'
if (-not (Test-Path -LiteralPath $PackSource -PathType Container)) {
    throw "Preset pack directory is missing: $PackSource"
}
foreach ($PackFile in Get-ChildItem -LiteralPath $PackSource -File -Filter '*.json' | Sort-Object Name) {
    $RelativePack = 'presets/packs/' + $PackFile.Name
    [void]$RuntimeFiles.Add($RelativePack)
    $Pack = Get-Content -Raw -LiteralPath $PackFile.FullName | ConvertFrom-Json
    foreach ($Step in @($Pack.steps)) {
        foreach ($Token in @($Step.command)) {
            if ($Token -is [string] -and $Token -match '^\{root\}/([^{}]+)$') {
                [void]$RuntimeFiles.Add($Matches[1])
            }
        }
    }
}

foreach ($RelativePath in $RuntimeFiles | Sort-Object) {
    Copy-ProjectFile -SourceRelativePath $RelativePath
}

Get-ChildItem -LiteralPath $Stage -Recurse -Directory -Filter '__pycache__' |
    Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $Stage -Recurse -File |
    Where-Object { $_.Extension -in @('.pyc', '.pyo') } |
    Remove-Item -Force

& (Join-Path $Stage 'packaging\bootstrap-runtime.ps1') `
    -BundleRoot $Stage `
    -SkipEditor
$BootstrapExitCode = $LASTEXITCODE
if ($BootstrapExitCode -ne 0) {
    throw "Portable runtime bootstrap failed with exit code $BootstrapExitCode"
}
Remove-Item -LiteralPath (Join-Path $Stage 'runtime\downloads') -Recurse -Force -ErrorAction SilentlyContinue

$ThirdPartyRoot = Join-Path $Stage 'runtime\third_party'
New-Item -ItemType Directory -Force -Path $ThirdPartyRoot | Out-Null
@'
GBFR-Save-Editor is intentionally not bundled because the pinned upstream
revision does not contain an explicit redistribution license. Double-click
RelinkSaveForge.cmd to download the fixed, SHA-256-verified upstream checkout.
'@ | Set-Content -LiteralPath (Join-Path $ThirdPartyRoot 'README.txt') -Encoding utf8

$ManifestPath = Join-Path $Stage 'SHA256SUMS.json'
$ManifestFiles = Get-ChildItem -LiteralPath $Stage -Recurse -File |
    Where-Object { $_.FullName -ne $ManifestPath } |
    Sort-Object FullName |
    ForEach-Object {
        [ordered]@{
            path = $_.FullName.Substring($Stage.Length + 1).Replace('\', '/')
            size = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        }
    }
[ordered]@{
    schema_version = 1
    product = 'Relink Save Forge'
    version = $Version
    generated_utc = [DateTime]::UtcNow.ToString('o')
    files = @($ManifestFiles)
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ManifestPath -Encoding utf8

Compress-Archive -LiteralPath $Stage -DestinationPath $ZipPath -CompressionLevel Optimal
$SmokeRoot = Join-Path $OutputRoot ('.bundle-smoke-' + [Guid]::NewGuid().ToString('N'))
try {
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $SmokeRoot -Force
    $Launchers = @(
        Get-ChildItem -LiteralPath $SmokeRoot -Recurse -File -Filter 'RelinkSaveForge.cmd'
    )
    if ($Launchers.Count -ne 1) {
        throw "Expected one packaged launcher, found $($Launchers.Count)"
    }
    $ExtractedBundle = $Launchers[0].Directory.FullName
    $ExtractedPython = Join-Path $ExtractedBundle 'runtime\python\python.exe'
    $ExtractedLauncher = Join-Path $ExtractedBundle 'app\launcher.py'
    if (-not (Test-Path -LiteralPath $ExtractedPython -PathType Leaf)) {
        throw "Packaged portable Python is missing: $ExtractedPython"
    }
    $PresetListing = & $ExtractedPython -B -I $ExtractedLauncher --list-presets 2>&1
    $SmokeExitCode = $LASTEXITCODE
    if ($SmokeExitCode -ne 0) {
        $SmokeDetails = ($PresetListing | Out-String).Trim()
        throw "Packaged --list-presets smoke test failed with exit code $SmokeExitCode. $SmokeDetails"
    }
    if (@($PresetListing).Count -lt 1) {
        throw 'Packaged --list-presets smoke test returned no presets'
    }
    Write-Host "Verified packaged preset listing ($(@($PresetListing).Count) entries)."
} catch {
    Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
    throw
} finally {
    Remove-Item -LiteralPath $SmokeRoot -Recurse -Force -ErrorAction SilentlyContinue
}
$ZipHash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash
Write-Host "Bundle: $ZipPath"
Write-Host "SHA-256: $ZipHash"
