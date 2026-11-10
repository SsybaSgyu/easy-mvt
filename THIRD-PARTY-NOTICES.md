# Third-party software

Easy MVT's own code is MIT-licensed (see [LICENSE](LICENSE)). It builds on the following
projects, which keep their own licenses.

## Bundled in the installers

| Project | License | Notes |
|---|---|---|
| [Electron](https://github.com/electron/electron) | MIT | Desktop shell. Its license and Chromium's notices ship inside the app (`LICENSE.electron.txt`, `LICENSES.chromium.html`). |
| [uv](https://github.com/astral-sh/uv) by Astral | MIT or Apache-2.0 (dual) | Pinned, checksum-verified binary in `Resources/bin`. Used to install Python and MVT. |

## Downloaded when the app runs (not redistributed)

| Project | License | Notes |
|---|---|---|
| [Mobile Verification Toolkit (MVT)](https://github.com/mvt-project/mvt) | [MVT License 1.1](https://github.com/mvt-project/mvt/blob/main/LICENSE) | Does all the forensic work. Installed from PyPI into the app's own data folder. **Its license only allows consensual forensic analysis** (see below). |
| Python ([python-build-standalone](https://github.com/astral-sh/python-build-standalone)) | PSF License | Private Python runtime, fetched by uv. |
| Spyware indicators (STIX2 files) | Set by each publisher | Downloaded by MVT from public indicator repositories, e.g. Amnesty International's Security Lab. |

## Artwork

| Project | License | Notes |
|---|---|---|
| [Anta](https://github.com/Typedesigners/Anta-Regular) font | SIL Open Font License 1.1 | Used for the "EASY MVT" lettering in the app icon, converted to outlines. The font file itself is not included. |

## Trademarks

"Amnesty International" and "MVT" are names of their respective owners. This project is
unofficial and not affiliated with or endorsed by them.
