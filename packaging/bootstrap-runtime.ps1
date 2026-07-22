[CmdletBinding()]
param(
    [Parameter()]
    [string]$BundleRoot = (Split-Path -Parent $PSScriptRoot),

    [Parameter()]
    [switch]$SkipPython,

    [Parameter()]
    [switch]$SkipEditor,

    [Parameter()]
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PythonVersion = '3.11.9'
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$PythonSha256 = '009D6BF7E3B2DDCA3D784FA09F90FE54336D5B60F0E0F305C37F400BF83CFD3B'
$EditorCommit = '8fdb4497fcf0cf67a4b122062a00f8ff07cc3942'
$EditorUrl = "https://codeload.github.com/xcier/GBFR-Save-Editor/zip/$EditorCommit"
$EditorSha256 = '9DA34D0714796FD45D2E51C00DD55BA1AB6F92C6289B115BBF706845660A9E5A'

$BundleRoot = [IO.Path]::GetFullPath($BundleRoot)
$RuntimeRoot = Join-Path $BundleRoot 'runtime'
$DownloadRoot = Join-Path $RuntimeRoot 'downloads'
$PythonRoot = Join-Path $RuntimeRoot 'python'
$EditorRoot = Join-Path $RuntimeRoot 'third_party\GBFR-Save-Editor'
$RuntimeLockPath = Join-Path $RuntimeRoot 'runtime-lock.json'
$PythonMarkerPath = Join-Path $PythonRoot '.relink-save-forge-runtime.json'
$EditorMarkerPath = Join-Path $EditorRoot '.relink-save-forge-source.json'

function Read-JsonObject {
    param([Parameter(Mandatory)] [string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Get-StringSha256 {
    param([Parameter(Mandatory)] [AllowEmptyString()] [string]$Value)

    $Hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $Digest = $Hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value))
    } finally {
        $Hasher.Dispose()
    }
    return ([BitConverter]::ToString($Digest)).Replace('-', '')
}

function Get-DirectoryTreeSha256 {
    param(
        [Parameter(Mandatory)] [string]$Root,
        [Parameter()] [string[]]$ExcludedRelativePaths = @()
    )

    $FullRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $Excluded = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($RelativePath in $ExcludedRelativePaths) {
        [void]$Excluded.Add($RelativePath.Replace('\', '/'))
    }
    $RowDigests = @(
        Get-ChildItem -LiteralPath $FullRoot -Recurse -File |
            ForEach-Object {
                $RelativePath = $_.FullName.Substring($FullRoot.Length + 1).Replace('\', '/')
                $Segments = $RelativePath.Split('/')
                $Extension = [IO.Path]::GetExtension($RelativePath)
                if (-not $Excluded.Contains($RelativePath) `
                    -and -not ($Segments -contains '__pycache__') `
                    -and $Extension -notin @('.pyc', '.pyo')) {
                    $FileSha256 = (
                        Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256
                    ).Hash.ToUpperInvariant()
                    Get-StringSha256 `
                        -Value "$RelativePath`0$($_.Length)`0$FileSha256"
                }
            }
    )
    [Array]::Sort($RowDigests, [StringComparer]::Ordinal)
    $Canonical = [string]::Join(
        "`n",
        [string[]]$RowDigests
    )
    if ($RowDigests.Count -gt 0) {
        $Canonical += "`n"
    }
    return Get-StringSha256 -Value $Canonical
}

function Test-RuntimeLockMetadata {
    param([Parameter()] $Lock)

    return $null -ne $Lock `
        -and $Lock.schema_version -eq 1 `
        -and $Lock.python.version -eq $PythonVersion `
        -and $Lock.python.url -eq $PythonUrl `
        -and $Lock.python.sha256 -eq $PythonSha256 `
        -and $Lock.editor.repository -eq 'https://github.com/xcier/GBFR-Save-Editor' `
        -and $Lock.editor.commit -eq $EditorCommit `
        -and $Lock.editor.url -eq $EditorUrl `
        -and $Lock.editor.sha256 -eq $EditorSha256
}

function Test-PythonIdentity {
    param([Parameter()] $Lock)

    $Marker = Read-JsonObject -Path $PythonMarkerPath
    $PythonExecutable = Join-Path $PythonRoot 'python.exe'
    if (-not (Test-RuntimeLockMetadata -Lock $Lock) `
        -or $Lock.python.installed -ne $true `
        -or -not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf) `
        -or $null -eq $Marker `
        -or $Marker.schema_version -ne 1 `
        -or $Marker.component -ne 'portable-python' `
        -or $Marker.version -ne $PythonVersion `
        -or $Marker.url -ne $PythonUrl `
        -or $Marker.archive_sha256 -ne $PythonSha256 `
        -or $Marker.architecture -ne 'amd64') {
        return $false
    }
    $ExecutableSha256 = (Get-FileHash -LiteralPath $PythonExecutable -Algorithm SHA256).Hash.ToUpperInvariant()
    $TreeSha256 = Get-DirectoryTreeSha256 `
        -Root $PythonRoot `
        -ExcludedRelativePaths @('.relink-save-forge-runtime.json')
    return $Marker.executable_sha256 -eq $ExecutableSha256 `
        -and $Marker.tree_sha256 -eq $TreeSha256
}

function Test-EditorIdentity {
    param([Parameter()] $Lock)

    $Marker = Read-JsonObject -Path $EditorMarkerPath
    $EditorCore = Join-Path $EditorRoot 'gbfr_editor\core\gbfr_save.py'
    if (-not (Test-RuntimeLockMetadata -Lock $Lock) `
        -or $Lock.editor.installed -ne $true `
        -or -not (Test-Path -LiteralPath $EditorCore -PathType Leaf) `
        -or $null -eq $Marker `
        -or $Marker.schema_version -ne 1 `
        -or $Marker.component -ne 'gbfr-save-editor' `
        -or $Marker.repository -ne 'https://github.com/xcier/GBFR-Save-Editor' `
        -or $Marker.commit -ne $EditorCommit `
        -or $Marker.url -ne $EditorUrl `
        -or $Marker.archive_sha256 -ne $EditorSha256) {
        return $false
    }
    $CoreSha256 = (Get-FileHash -LiteralPath $EditorCore -Algorithm SHA256).Hash.ToUpperInvariant()
    $TreeSha256 = Get-DirectoryTreeSha256 `
        -Root $EditorRoot `
        -ExcludedRelativePaths @('.relink-save-forge-source.json')
    return $Marker.core_sha256 -eq $CoreSha256 `
        -and $Marker.tree_sha256 -eq $TreeSha256
}

function Get-VerifiedDownload {
    param(
        [Parameter(Mandatory)] [string]$Url,
        [Parameter(Mandatory)] [string]$Destination,
        [Parameter(Mandatory)] [string]$ExpectedSha256
    )

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    if (-not (Test-Path -LiteralPath $Destination)) {
        Write-Host "Downloading $Url"
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Destination
    }
    $Actual = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($Actual -ne $ExpectedSha256) {
        Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
        throw "SHA-256 mismatch for $Url. Expected $ExpectedSha256, found $Actual."
    }
}

function Install-ZipAtomically {
    param(
        [Parameter(Mandatory)] [string]$Archive,
        [Parameter(Mandatory)] [string]$Destination,
        [Parameter()] [string]$ExtractedSubdirectory,
        [Parameter()] [switch]$ReplaceExisting
    )

    $Parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    $Temporary = Join-Path $Parent ('.bootstrap-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $Temporary | Out-Null
    try {
        Expand-Archive -LiteralPath $Archive -DestinationPath $Temporary -Force
        $Source = if ($ExtractedSubdirectory) {
            Join-Path $Temporary $ExtractedSubdirectory
        } else {
            $Temporary
        }
        if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
            throw "Expected extracted directory was not found: $Source"
        }
        if (Test-Path -LiteralPath $Destination) {
            if (-not $ReplaceExisting) {
                throw "Destination already exists: $Destination"
            }
            Remove-Item -LiteralPath $Destination -Recurse -Force
        }
        if ($ExtractedSubdirectory) {
            Move-Item -LiteralPath $Source -Destination $Destination
        } else {
            New-Item -ItemType Directory -Path $Destination | Out-Null
            Get-ChildItem -Force -LiteralPath $Temporary | Move-Item -Destination $Destination
        }
    } finally {
        Remove-Item -LiteralPath $Temporary -Recurse -Force -ErrorAction SilentlyContinue
    }
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot, $DownloadRoot | Out-Null
$ExistingLock = Read-JsonObject -Path $RuntimeLockPath
$LockMetadataTrusted = Test-RuntimeLockMetadata -Lock $ExistingLock
$PythonInstalled = Test-PythonIdentity -Lock $ExistingLock
$EditorInstalled = Test-EditorIdentity -Lock $ExistingLock
$StateChanged = -not $LockMetadataTrusted

if (-not $SkipPython) {
    $PythonExecutable = Join-Path $PythonRoot 'python.exe'
    if ($Force -or -not $PythonInstalled) {
        $PythonArchive = Join-Path $DownloadRoot "python-$PythonVersion-embed-amd64.zip"
        Get-VerifiedDownload -Url $PythonUrl -Destination $PythonArchive -ExpectedSha256 $PythonSha256
        Install-ZipAtomically `
            -Archive $PythonArchive `
            -Destination $PythonRoot `
            -ReplaceExisting
        $PythonExecutableSha256 = (
            Get-FileHash -LiteralPath $PythonExecutable -Algorithm SHA256
        ).Hash.ToUpperInvariant()
        $PythonTreeSha256 = Get-DirectoryTreeSha256 -Root $PythonRoot
        [ordered]@{
            schema_version = 1
            component = 'portable-python'
            version = $PythonVersion
            url = $PythonUrl
            archive_sha256 = $PythonSha256
            architecture = 'amd64'
            executable_sha256 = $PythonExecutableSha256
            tree_sha256 = $PythonTreeSha256
        } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $PythonMarkerPath -Encoding utf8
        $StateChanged = $true
    }
    if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
        throw "Portable Python installation failed: $PythonExecutable"
    }
    $PythonProbe = "import platform, struct, sys; actual = platform.python_version(); expected = sys.argv[1]; bits = struct.calcsize('P') * 8; assert actual == expected, (actual, expected); assert bits == 64, bits; print('Python {} ({}-bit)'.format(actual, bits))"
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $ProbeOutput = & $PythonExecutable -I -c $PythonProbe $PythonVersion 2>&1
        $ProbeExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    if ($ProbeExitCode -ne 0) {
        $ProbeDetails = ($ProbeOutput | Out-String).Trim()
        throw "Portable Python validation failed with exit code $ProbeExitCode. $ProbeDetails"
    }
    Write-Host "Verified portable runtime: $ProbeOutput"
    $PythonInstalled = $true
}

if (-not $SkipEditor) {
    $EditorCore = Join-Path $EditorRoot 'gbfr_editor\core\gbfr_save.py'
    if ($Force -or -not $EditorInstalled) {
        $EditorArchive = Join-Path $DownloadRoot "GBFR-Save-Editor-$EditorCommit.zip"
        Get-VerifiedDownload -Url $EditorUrl -Destination $EditorArchive -ExpectedSha256 $EditorSha256
        Install-ZipAtomically `
            -Archive $EditorArchive `
            -Destination $EditorRoot `
            -ExtractedSubdirectory "GBFR-Save-Editor-$EditorCommit" `
            -ReplaceExisting
        $EditorCoreSha256 = (
            Get-FileHash -LiteralPath $EditorCore -Algorithm SHA256
        ).Hash.ToUpperInvariant()
        $EditorTreeSha256 = Get-DirectoryTreeSha256 -Root $EditorRoot
        [ordered]@{
            schema_version = 1
            component = 'gbfr-save-editor'
            repository = 'https://github.com/xcier/GBFR-Save-Editor'
            commit = $EditorCommit
            url = $EditorUrl
            archive_sha256 = $EditorSha256
            core_sha256 = $EditorCoreSha256
            tree_sha256 = $EditorTreeSha256
        } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $EditorMarkerPath -Encoding utf8
        $StateChanged = $true
    }
    if (-not (Test-Path -LiteralPath $EditorCore -PathType Leaf)) {
        throw "GBFR-Save-Editor bootstrap failed: $EditorCore"
    }
    $EditorInstalled = $true
}

if ($null -eq $ExistingLock `
    -or $ExistingLock.python.installed -ne $PythonInstalled `
    -or $ExistingLock.editor.installed -ne $EditorInstalled) {
    $StateChanged = $true
}

$Lock = [ordered]@{
    schema_version = 1
    generated_utc = [DateTime]::UtcNow.ToString('o')
    python = [ordered]@{
        version = $PythonVersion
        url = $PythonUrl
        sha256 = $PythonSha256
        installed = $PythonInstalled
    }
    editor = [ordered]@{
        repository = 'https://github.com/xcier/GBFR-Save-Editor'
        commit = $EditorCommit
        url = $EditorUrl
        sha256 = $EditorSha256
        installed = $EditorInstalled
        distribution_policy = 'Downloaded from upstream at bootstrap time; not bundled by this project.'
    }
}
if ($StateChanged -or -not (Test-Path -LiteralPath $RuntimeLockPath -PathType Leaf)) {
    $Lock | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $RuntimeLockPath -Encoding utf8
}

Write-Host "Runtime ready under $RuntimeRoot"
