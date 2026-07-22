# Releasing Relink Save Forge

This document defines the release contract for the Windows one-click bundle.
The intended GitHub repository name is `relink-save-forge` and the product name
shown to users is **Relink Save Forge**.

## Release artifacts

The workflow uploads exactly these release assets:

- `RelinkSaveForge-win-x64-v<version>.zip`, produced by
  `packaging/build-windows-bundle.ps1`;
- `SHA256SUMS.txt`, containing the lowercase SHA-256 digest and ZIP filename.

The ZIP contains Relink Save Forge code, catalogs, presets, documentation, and
the Windows launcher, plus the pinned and verified CPython embeddable runtime.
It must not contain user save files, audit output, local paths, Steam IDs, raw
game databases, `GBFRDataTools`, `GBFRelinkMod`, or `GBFR-Save-Editor`.

`GBFR-Save-Editor` is acquired by the bootstrap on the first operation that
needs to open a save and is not redistributed in the bundle. See
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for the fixed upstream
revision and licensing boundary.

## Versioning

Use semantic versions. Stable releases use tags such as `v1.0.0`; prereleases
may use tags such as `v1.1.0-rc.1`. The version passed to the packaging script
does not include the leading `v`.

Do not move or reuse a published tag. If an asset is wrong, fix the source and
publish a new patch version.

## Validation and tests

The repository intentionally has no `requirements.txt`. Repository validation,
unit tests, and packaging use Python 3.11.9's standard library and PowerShell;
the release workflow must not add an unconditional `pip install` step.

Run these commands from the repository root before tagging:

```powershell
python scripts/validate_repository.py
python -m unittest discover -s tests -p "test_*.py"
```

Build a local candidate with:

```powershell
./packaging/build-windows-bundle.ps1 `
  -Version 1.0.0 `
  -OutputDirectory dist
```

Confirm that `dist` contains exactly one ZIP. Generate and verify its checksum:

```powershell
$zip = Get-ChildItem -LiteralPath dist -Filter "*.zip" -File
$hash = (Get-FileHash -LiteralPath $zip.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  $($zip.Name)" | Set-Content -LiteralPath dist/SHA256SUMS.txt -Encoding ascii
```

## GitHub Actions behavior

`.github/workflows/release-windows.yml` supports four paths:

- **Pull requests** validate, test, build, hash, and smoke-test the Windows ZIP
  without creating a GitHub Release.
- **Pushes to `main`** run the same validation and package smoke path so the
  default branch remains continuously releasable.
- **Manual dispatch** validates, tests, builds, hashes, and uploads a temporary
  workflow artifact. It does not create a GitHub Release.
- **A pushed `v*` tag** performs the same build and then creates the matching
  GitHub Release with the ZIP and `SHA256SUMS.txt`. It refuses to replace an
  existing Release.

The build job has read-only repository permission. Only the tag-only publish
job receives `contents: write`, and it publishes the exact artifact produced by
the preceding build job.

## Release procedure

1. Start from a clean checkout of `main` and pull the intended release commit.
2. Review `THIRD_PARTY_NOTICES.md`, pinned dependency revisions, preset
   manifests, and compatibility notes for the supported game version.
3. Run repository validation and all unit tests.
4. Run the workflow manually with the intended version and download its
   temporary artifact.
5. Extract the ZIP into a new directory on a Windows machine that has no local
   editor checkout beside it; confirm that the packaged CPython runtime starts.
6. Confirm first-run bootstrap behavior, upstream pin verification, preset
   listing, dry-run output, backup creation, and explicit apply behavior.
7. Re-run a dry run against the produced candidate and confirm idempotent output
   where the selected preset promises idempotency.
8. Verify that no save, Steam ID, local path, raw database, editor checkout,
   GBFRDataTools checkout, or GBFRelinkMod checkout is present in the ZIP, and
   that the included Python runtime matches its pin.
9. Commit the final release metadata, then create and push an immutable signed
   or annotated tag such as `v1.0.0`.
10. Wait for the tag workflow to finish, download both Release assets, and
    independently verify the published ZIP against `SHA256SUMS.txt`.

## First-run dependency policy

The release asset includes Python but bootstraps the external save editor:

- During packaging, CPython 3.11.9 x64 embeddable is downloaded from its
  official distribution URL, must match SHA-256
  `009D6BF7E3B2DDCA3D784FA09F90FE54336D5B60F0E0F305C37F400BF83CFD3B`.
- The release bundle contains that verified Python runtime, so users do not
  need a system Python installation.
- Preset listing works without the editor and without network access. The first
  operation that opens a save requires HTTPS access to `github.com` to obtain
  the editor; subsequent operations use the verified checkout under the
  extracted bundle's `runtime/third_party` directory.
- `xcier/GBFR-Save-Editor` is downloaded from its upstream repository at commit
  `8fdb4497fcf0cf67a4b122062a00f8ff07cc3942`; its codeload archive must match
  SHA-256
  `9DA34D0714796FD45D2E51C00DD55BA1AB6F92C6289B115BBF706845660A9E5A`.
- Neither dependency may silently float to a branch head or latest release.
- Failed downloads, revision mismatches, or integrity failures must stop before
  any save is opened or modified.
- Run logs, backups, and transaction state belong under the user's local
  application-data directory; only the pinned runtime dependencies live under
  the extracted release directory.

Changing either dependency pin is a release-significant change. Review the
upstream diff, update the notices and tests, and publish a new version rather
than changing a previously published bundle in place.

## Release-notes template

Use concrete compatibility and safety statements. Do not claim support for a
game version or preset that was not exercised by the release candidate.

```markdown
## Relink Save Forge vX.Y.Z

### Included

- Windows one-click launcher
- Auditable preset manifests
- Offline backup, dry-run, apply, and verification flow

### Compatibility

- Granblue Fantasy: Relink: <tested game version>
- Windows: <tested versions>

### Dependency pins

- GBFRDataTools data reference: `571a1d1ce71c17601684894dad186269c0fed1dc`
- GBFRelinkMod research reference: `c9bd8350e6deb3a3034194fe6fbf62cd453989e9`
- GBFR-Save-Editor: `8fdb4497fcf0cf67a4b122062a00f8ff07cc3942`
- CPython: 3.11.9 x64 embeddable (included)

### Safety

The tool works on an offline copy, creates a backup before apply, and stops on
hash, dependency, or verification failure. Keep the game closed while applying
a preset.

### Verification

Download the ZIP and `SHA256SUMS.txt`, then compare the ZIP's SHA-256 before
extracting it.
```
