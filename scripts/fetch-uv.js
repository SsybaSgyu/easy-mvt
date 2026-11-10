#!/usr/bin/env node
// Downloads a pinned `uv` binary for bundling into the desktop app and checks it against
// SHA-256 checksums hard-coded below, so a tampered release can't slip in.
//   node scripts/fetch-uv.js                 -> current platform/arch
//   node scripts/fetch-uv.js darwin x64      -> explicit target
// Output: vendor/uv/<platform>-<arch>/uv[.exe]
const { execFileSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const https = require("https");
const os = require("os");
const path = require("path");

// To update uv: change the version and copy the new checksums from the
// *.sha256 files of that release on https://github.com/astral-sh/uv/releases (keep app.py in sync).
const UV_VERSION = "0.13.7";
const SHA256 = {
  "uv-aarch64-apple-darwin.tar.gz": "81b811b57e4624df7ed3aac148e0359100d9962f3d9dce00aec533dc2e086731",
  "uv-x86_64-apple-darwin.tar.gz": "20c47e4c1f630b8abaff8c291fa9b9aa300fef92c4fbff95adf198b55287cda7",
  "uv-x86_64-unknown-linux-gnu.tar.gz": "c5221ff41b7892bfc223543883600125f6a47fe845093e0f5bdff4f359196d83",
  "uv-aarch64-unknown-linux-gnu.tar.gz": "71279f8d3c67241e807b9d8db872d1bf541579fb9f08b7d1eb1682196d1e57dc",
  "uv-x86_64-pc-windows-msvc.zip": "0cdc5b38fe5a8168ed6a25814f5ba43c21d313dd3187314fccb37b7cec46e021",
  "uv-aarch64-pc-windows-msvc.zip": "505d9e65e7692bac3ddf98c66754aa26c5050c409c184af30a881a0a72cc85e3",
};
const platform = process.argv[2] || process.platform;
const arch = process.argv[3] || process.arch;

const TARGETS = {
  "darwin-arm64": "aarch64-apple-darwin",
  "darwin-x64": "x86_64-apple-darwin",
  "linux-x64": "x86_64-unknown-linux-gnu",
  "linux-arm64": "aarch64-unknown-linux-gnu",
  "win32-x64": "x86_64-pc-windows-msvc",
  "win32-arm64": "aarch64-pc-windows-msvc",
};
const target = TARGETS[`${platform}-${arch}`];
if (!target) { console.error(`No uv build for ${platform}-${arch}`); process.exit(1); }

const isWin = platform === "win32";
const asset = `uv-${target}.${isWin ? "zip" : "tar.gz"}`;
const base = `https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${asset}`;
const outDir = path.join(__dirname, "..", "vendor", "uv", `${platform}-${arch}`);
const exe = isWin ? "uv.exe" : "uv";

function get(url, redirects = 5) {
  return new Promise((resolve, reject) => {
    https.get(url, { headers: { "User-Agent": "easy-mvt-build" } }, (res) => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode) && redirects > 0) {
        res.resume();
        return resolve(get(new URL(res.headers.location, url).toString(), redirects - 1));
      }
      if (res.statusCode !== 200) { res.resume(); return reject(new Error(`${url} -> HTTP ${res.statusCode}`)); }
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve(Buffer.concat(chunks)));
    }).on("error", reject);
  });
}

function findFile(dir, name) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) { const r = findFile(p, name); if (r) return r; }
    else if (e.name === name) return p;
  }
  return null;
}

(async () => {
  if (fs.existsSync(path.join(outDir, exe))) { console.log(`uv already present in ${outDir}`); return; }
  console.log(`Downloading ${base}`);
  const expected = SHA256[asset];
  if (!expected) throw new Error(`No pinned checksum for ${asset}`);
  const archive = await get(base);
  const actual = crypto.createHash("sha256").update(archive).digest("hex");
  if (expected !== actual) throw new Error(`Checksum mismatch for ${asset}: expected ${expected}, got ${actual}`);
  console.log(`SHA-256 OK ${actual}`);

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "uv-"));
  const file = path.join(tmp, asset);
  fs.writeFileSync(file, archive);
  // Windows' own tar.exe (bsdtar) can unpack .zip; Git's GNU tar, often first on PATH, can't.
  const tar = process.platform === "win32"
    ? path.join(process.env.SystemRoot || "C:\\Windows", "System32", "tar.exe")
    : "tar";
  execFileSync(tar, [isWin ? "-xf" : "-xzf", file, "-C", tmp]);
  const bin = findFile(tmp, exe);
  if (!bin) throw new Error(`${exe} not found in ${asset}`);
  fs.mkdirSync(outDir, { recursive: true });
  fs.copyFileSync(bin, path.join(outDir, exe));
  if (!isWin) fs.chmodSync(path.join(outDir, exe), 0o755);
  fs.rmSync(tmp, { recursive: true, force: true });
  console.log(`uv ${UV_VERSION} -> ${path.join(outDir, exe)}`);
})().catch((e) => { console.error(e.message); process.exit(1); });
