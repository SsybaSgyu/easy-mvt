// Easy MVT desktop shell.
// Starts the Python backend (app.py) with the bundled `uv`, which also fetches a
// private Python on first launch, then shows the UI in a locked-down window.
const { app, BrowserWindow, dialog, ipcMain, shell, Menu } = require("electron");
const { spawn } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");

const IS_WIN = process.platform === "win32";
let win = null;
let backend = null;
let origin = null;
// Access token for the local backend. Created here and given to the page only through the
// preload bridge, so other programs on this computer can't read it from the backend.
const TOKEN = crypto.randomBytes(32).toString("base64url");
let quitting = false;

// ---------- paths ----------
const backendDir = () => (app.isPackaged ? path.join(process.resourcesPath, "backend") : path.join(__dirname, ".."));
const uvPath = () => {
  const exe = IS_WIN ? "uv.exe" : "uv";
  return app.isPackaged
    ? path.join(process.resourcesPath, "bin", exe)
    : path.join(__dirname, "..", "vendor", "uv", `${process.platform}-${process.arch}`, exe);
};
const dataDir = () => app.getPath("userData");

// ---------- helpers ----------
function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

function waitForBackend(port, timeoutMs) {
  const until = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get({ host: "127.0.0.1", port, path: "/", timeout: 2000 }, (res) => {
        res.resume();
        res.statusCode === 200 ? resolve() : retry();
      });
      req.on("error", retry);
      req.on("timeout", () => { req.destroy(); retry(); });
    };
    const retry = () => {
      if (backend && backend.exitCode !== null) return reject(new Error("The scanner engine stopped unexpectedly."));
      if (Date.now() > until) return reject(new Error("The scanner engine did not start in time."));
      setTimeout(attempt, 400);
    };
    attempt();
  });
}

async function startBackend() {
  const port = await freePort();
  const data = dataDir();
  fs.mkdirSync(data, { recursive: true });
  const log = fs.createWriteStream(path.join(data, "backend.log"), { flags: "w" });
  const tools = path.join(data, ".tools");
  const env = {
    ...process.env,
    MVT_UI_PORT: String(port),
    MVT_UI_DATA: data,
    MVT_UI_UV: uvPath(),
    MVT_UI_EMBEDDED: "1",
    MVT_UI_TOKEN: TOKEN,
    UV_PYTHON_INSTALL_DIR: path.join(tools, "python"),
    UV_CACHE_DIR: path.join(tools, "cache"),
    UV_NO_PROGRESS: "1",
    PYTHONUNBUFFERED: "1",
  };
  const args = ["run", "--no-project", "--python", "3.12", path.join(backendDir(), "app.py"), "--no-browser"];
  backend = spawn(uvPath(), args, { env, cwd: data, stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
  backend.stdout.pipe(log);
  backend.stderr.pipe(log);
  backend.on("exit", (code) => {
    log.write(`\n[backend exited with ${code}]\n`);
    if (!quitting && win) {
      dialog.showErrorBox("Easy MVT", `The scanner engine stopped (code ${code}).\nDetails: ${path.join(data, "backend.log")}`);
      app.quit();
    }
  });
  // first launch downloads a private Python (~25 MB), so be patient
  await waitForBackend(port, 5 * 60 * 1000);
  return `http://127.0.0.1:${port}`;
}

function stopBackend() {
  if (!backend || backend.exitCode !== null) return;
  // The backend exits when its stdin closes, after stopping the scan and deleting any
  // decrypted backup. Deleting a large backup can take a while, so give it time; if it's
  // still running, SIGTERM triggers the same cleanup (and the next start sweeps leftovers).
  try { backend.stdin.end(); } catch {}
  setTimeout(() => { try { backend.kill(); } catch {} }, 10000);
}

// ---------- window ----------
function createWindow() {
  win = new BrowserWindow({
    width: 1180, height: 860, minWidth: 720, minHeight: 560,
    backgroundColor: "#000000", title: "Easy MVT", show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
      webSecurity: true, spellcheck: false,
    },
  });
  win.once("ready-to-show", () => win.show());

  // Never navigate away from the local UI; open web links in the real browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (!isOurOrigin(url)) {
      e.preventDefault();
      if (/^https:\/\//.test(url)) shell.openExternal(url);
    }
  });
  win.on("closed", () => { win = null; });
  win.loadFile(path.join(__dirname, "loading.html"));
}

// Exact origin match: a prefix check would also accept e.g. port 51234 when ours is 5123.
function isOurOrigin(url) {
  if (!origin || !url) return false;
  try { return new URL(url).origin === origin; } catch { return false; }
}
const fromUI = (event) => !!(event.senderFrame && isOurOrigin(event.senderFrame.url));

ipcMain.handle("token", (event) => (fromUI(event) ? TOKEN : null));

ipcMain.handle("pick", async (event, kind) => {
  if (!fromUI(event)) return null;
  const r = await dialog.showOpenDialog(win, {
    title: kind === "folder" ? "Choose the folder to scan" : "Choose the file to scan",
    properties: [kind === "folder" ? "openDirectory" : "openFile"],
  });
  return r.canceled || !r.filePaths.length ? null : r.filePaths[0];
});
ipcMain.handle("quit", (event) => { if (fromUI(event)) app.quit(); });

// ---------- lifecycle ----------
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });

  app.whenReady().then(async () => {
    if (process.platform !== "darwin") Menu.setApplicationMenu(null);
    createWindow();
    try {
      origin = await startBackend();
      if (win) await win.loadURL(origin + "/");
    } catch (err) {
      dialog.showErrorBox("Easy MVT could not start",
        `${err.message}\n\nThe first start needs an internet connection.\nDetails: ${path.join(dataDir(), "backend.log")}`);
      app.quit();
    }
  });

  app.on("before-quit", () => { quitting = true; stopBackend(); });
  app.on("window-all-closed", () => app.quit());
}
