# Third-Party Notices

Relink Save Forge is an independent community project. It is not affiliated
with, endorsed by, or sponsored by Cygames, Inc. or any other owner of
Granblue Fantasy: Relink. Game names, trademarks, game data, and other
third-party materials remain the property of their respective owners.

The repository's MIT license covers only original Relink Save Forge code and
documentation. It does not relicense game data, user save files, or the
projects listed below.

## Nenkai/GBFRDataTools

- Upstream: <https://github.com/Nenkai/GBFRDataTools>
- Data-extraction reference commit:
  `571a1d1ce71c17601684894dad186269c0fed1dc`
- License: MIT

Relink Save Forge uses GBFRDataTools as the documented source for database
extraction and for the save-hash behavior ported into this repository. The
upstream source tree, binaries, and raw extracted game databases are not
included in the Windows bundle. Generated catalogs in this repository record
their source hashes and derivation method so they can be audited independently.

The GBFRDataTools license notice follows:

> MIT License
>
> Copyright (c) 2024 Nenkai
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in
> all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

## alexfrljuckic/GBFRelinkMod

- Upstream: <https://github.com/alexfrljuckic/GBFRelinkMod>
- Research reference commit: `c9bd8350e6deb3a3034194fe6fbf62cd453989e9`

GBFRelinkMod was consulted only as a research reference while auditing game
structures and behavior. Relink Save Forge does not include or redistribute
its source, binaries, patches, or assets, and does not relicense any part of
that project.

No `LICENSE`, `COPYING`, or `NOTICE` file was present in the audited upstream
checkout at the pinned commit. Relink Save Forge therefore makes no license
grant for GBFRelinkMod; users must review the upstream project's terms before
using material obtained from it.

## xcier/GBFR-Save-Editor

- Upstream: <https://github.com/xcier/GBFR-Save-Editor>
- Runtime commit: `8fdb4497fcf0cf67a4b122062a00f8ff07cc3942`
- Pinned codeload archive SHA-256:
  `9DA34D0714796FD45D2E51C00DD55BA1AB6F92C6289B115BBF706845660A9E5A`

GBFR-Save-Editor is not included in Relink Save Forge source archives or
Windows release assets. The first operation that needs to open a save invokes
the bootstrap, which downloads the fixed upstream revision into the extracted
bundle's runtime directory. Relink Save Forge verifies the pinned codeload
archive before extraction and does not redistribute or relicense that
checkout. Users should review and comply with the upstream project's terms
before using it.

No `LICENSE` or `COPYING` file was present in the audited upstream checkout at
the pinned commit. This is an additional reason the project obtains the tool
from upstream at runtime instead of shipping a copy.

## Python runtime

The Windows packaging process obtains the official CPython 3.11.9 x64
embeddable distribution from
<https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip>,
requires SHA-256
`009D6BF7E3B2DDCA3D784FA09F90FE54336D5B60F0E0F305C37F400BF83CFD3B`
before extraction, and includes the verified runtime in the Windows bundle.
That runtime is governed by the Python Software Foundation License and other
notices published with CPython 3.11.9:
<https://docs.python.org/3/license.html>.
