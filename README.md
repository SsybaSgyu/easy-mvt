# Easy MVT

A friendly desktop app for Amnesty International's
[Mobile Verification Toolkit (MVT)](https://github.com/mvt-project/mvt): check an iPhone or
Android phone for traces of known spyware (Pegasus, Predator, …) **without using a terminal**.

> **Unofficial.** This project is not affiliated with or endorsed by Amnesty International
> or the MVT project. It's a graphical wrapper; all forensic work is done by MVT itself.

> **Only check phones with the owner's consent.** MVT's license allows only consensual
> forensic analysis. Checking someone else's phone without their permission is not allowed
> and is illegal in many countries.

## Download

Get the installer for your system from the [Releases](../../releases) page:

| System | File |
|---|---|
| macOS (Apple Silicon) | `Easy-MVT-x.y.z-mac-arm64.dmg` |
| macOS (Intel) | `Easy-MVT-x.y.z-mac-x64.dmg` |
| Windows | `Easy-MVT-x.y.z-win-x64.exe` *(experimental)* |
| Linux | `Easy-MVT-x.y.z-linux-x86_64.AppImage` |

**First start:** the app downloads a private Python runtime (~25 MB). Then click
**Install everything** to download MVT and the latest spyware indicators. Python, MVT and the
indicators stay in the app's own data folder; nothing is installed system-wide. Scan results
are saved in `Documents/MVT Results` (see below).

> Unsigned builds: on macOS, right-click the app → **Open** the first time (or *System Settings →
> Privacy & Security → Open Anyway*). On Windows, click *More info → Run anyway*.

## What it does

- **Preparation guide** with illustrations: Airplane Mode, SIM card, cable connection,
  encrypted iPhone backups, Android bug reports. Printable.
- **Finds iPhone backups automatically.** For encrypted backups, it decrypts to a temporary folder
  that is deleted after the scan, and passes the password via an environment variable, never on
  the command line.
- **Plain-language verdict** with next steps: red for matches with known spyware, orange for
  minor or unusual findings, green when nothing turned up. Every finding is explained, with
  what it is, what to do, and its individual entries.
- **"What does it check?"** lists every spyware family and the number of indicators of each type,
  read live from the downloaded STIX files, and every MVT module with the indicator types it
  compares, read live from the installed MVT's source.
- **Built-in terminal** shows every command that runs, and accepts `mvt-ios` / `mvt-android`
  commands (only those, no shell).

## Privacy & security

- Everything runs locally; phone data never leaves the computer.
- **Scan results are not encrypted.** Each scan writes a folder in `Documents/MVT Results` with
  the raw data MVT extracted (e.g. messages, browsing history, contacts). Keep it safe, share it
  only with people you trust (such as a helpline), and delete it when you no longer need it.
- Decrypted iPhone backups are written to a private temporary folder and deleted when the scan
  ends, is cancelled or the app quits. Leftovers from a crash are removed at the next start.
- The backend listens on `127.0.0.1` only, requires a per-session token, rejects foreign
  `Host` headers (DNS-rebinding protection) and can only run whitelisted MVT commands. In the
  desktop app the token is created by Electron and handed to the page through the preload
  bridge, never served by the backend. (Running `app.py` on its own in a browser embeds it in
  the page instead; that mode is meant for development.)
- The page has a strict Content-Security-Policy: only its own script may run.
- The Electron window uses `contextIsolation`, `sandbox`, no Node integration, and can't
  navigate away from the local UI.
- `uv` is pinned to one version and checked against SHA-256 checksums written into the
  source code. Python comes from python-build-standalone via uv. MVT is deliberately **not**
  pinned: "Install everything" always fetches the newest MVT from PyPI, so new detections arrive
  without waiting for an app update.

## How it's built

```
electron/        desktop shell: starts the backend with the bundled uv, opens the window
app.py           local backend (Python standard library only)
introspect.py    runs inside MVT's Python to describe which checks each module performs
static/          the interface (index.html + app.js, no external resources)
scripts/         fetch-uv.js: downloads a pinned, checksum-verified uv for bundling
build/icon.png   app icon
```

### Develop

```bash
npm install
npm start            # fetches uv for your platform, launches the app
# or without Electron:
python3 app.py       # opens http://127.0.0.1:8765 in your browser
```

### Build installers

```bash
npm run dist:mac     # or dist:win / dist:linux
```

Pushing a tag like `v0.1.0` runs `.github/workflows/release.yml`, which builds all platforms and
creates a **draft** GitHub Release with the installers and `SHA256SUMS.txt`.
Optional macOS signing/notarization: add the secrets `MAC_CERT_P12_BASE64`, `MAC_CERT_PASSWORD`,
`APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`.

## No guarantee

A clean result means no *known* spyware traces were found. It does not prove a phone is safe.
This software comes without any warranty (see [LICENSE](LICENSE)). If you think you're being
targeted, contact a digital-security helpline such as
[Access Now's](https://www.accessnow.org/help/).

## Security

Found a security problem? Please report it privately, see [SECURITY.md](SECURITY.md).

## License & credits

Easy MVT is made by **SsybaSgyu** ([github.com/SsybaSgyu](https://github.com/SsybaSgyu))
and released under the [MIT License](LICENSE). You may use, change and share it freely, as long
as you keep the copyright notice, i.e. the credit to the original author.

MVT is distributed under its own [MVT License 1.1](https://github.com/mvt-project/mvt/blob/main/LICENSE).
This app doesn't include MVT's code; it downloads MVT at runtime. For all third-party components
see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

## Feedback welcome

I'm happy about any kind of feedback: bug reports, ideas, things that were confusing, or
just a note that the app helped you. Please open an [issue](../../issues). For security
problems, use the private route in [SECURITY.md](SECURITY.md) instead.

<sub>Side note: I'm a vibe coder. The idea, refinement and testing are mine; most of the code
was written with [Claude](https://claude.ai) (Anthropic) as an AI coding assistant.</sub>
