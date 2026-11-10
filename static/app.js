// Easy MVT interface logic. Loaded by static/index.html.
// Desktop app: the token comes from Electron through the preload bridge and is never in the HTML.
// Standalone browser mode: the backend writes it into the mvt-token meta tag.
let TOKEN = document.querySelector('meta[name="mvt-token"]').content;
const tokenReady = (window.mvtDesktop && window.mvtDesktop.token)
  ? window.mvtDesktop.token().then(t => { TOKEN = t || TOKEN; })
  : Promise.resolve();
const $ = (id) => document.getElementById(id);
const state = { env: null, ios: null, amode: null, apath: null, lastScan: null, logNext: 0, poll: null, t0: 0,
                about: null, aboutScan: "iphone-backup", spin: 0 };

async function api(path, opts = {}) {
  await tokenReady;
  const res = await fetch(path, {
    method: opts.body !== undefined || opts.post ? "POST" : "GET",
    headers: { "X-Token": TOKEN, "Content-Type": "application/json" },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

function show(view) {
  for (const s of document.querySelectorAll("main > section")) s.classList.add("hidden");
  $("v-" + view).classList.remove("hidden");
  if (["home", "iphone", "android", "about", "guide"].includes(view)) history.replaceState(null, "", "#" + view);
  hideError();
  if (view === "iphone") loadBackups();
  if (view === "home") renderUpdStatus();
  if (view === "about") loadAbout();
  window.scrollTo(0, 0);
}
function showError(msg) { $("globalError").textContent = msg; $("globalError").classList.remove("hidden"); }
function hideError() { $("globalError").classList.add("hidden"); }
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
const fmt = (n) => Number(n).toLocaleString("en-US");

document.querySelectorAll("[data-go]").forEach(b => b.onclick = () => show(b.dataset.go));
document.querySelectorAll("[data-scroll]").forEach(b =>
  b.onclick = () => document.getElementById(b.dataset.scroll).scrollIntoView({ behavior: "smooth" }));
document.querySelectorAll("[data-print]").forEach(b => b.onclick = () => window.print());

/* ---------- picker ---------- */
// Inside the desktop app, Electron provides native dialogs on every OS.
const desktop = window.mvtDesktop || null;
const canPick = () => !!desktop || state.env.mac;
async function pick(kind) {
  if (desktop) return desktop.pick(kind);
  if (!state.env.mac) return null;
  try { return (await api("/api/pick?kind=" + kind)).path; } catch { return null; }
}

/* ---------- iPhone ---------- */
async function loadBackups() {
  $("ibLoading").classList.remove("hidden");
  ["ibPermission", "ibNone"].forEach(i => $(i).classList.add("hidden"));
  $("ibList").replaceChildren();
  const r = await api("/api/ios-backups");
  $("ibLoading").classList.add("hidden");
  if (r.problem === "permission") $("ibPermission").classList.remove("hidden");
  if (r.problem === "none") { $("ibNone").classList.remove("hidden"); $("ibHelp").open = true; }
  for (const b of r.backups) {
    const c = el("button", "choice");
    c.append(el("b", null, b.name));
    c.append(el("span", null, [b.model, b.date ? "backed up " + b.date : null, b.encrypted ? "encrypted" : "not encrypted"].filter(Boolean).join(" · ")));
    c.onclick = () => { document.querySelectorAll("#ibList .choice").forEach(x => x.classList.remove("selected")); c.classList.add("selected"); selectBackup(b); };
    $("ibList").append(c);
  }
  if (r.backups.length === 1) $("ibList").firstChild.click();
}
function selectBackup(b) {
  state.ios = b;
  $("ibSelected").classList.remove("hidden");
  $("ibSelName").textContent = b.name;
  $("ibSelLock").textContent = b.encrypted ? "[encrypted]" : "";
  $("ibSelMeta").textContent = b.encrypted ? "This backup is encrypted. Enter its password to check it."
    : "This backup is not encrypted. The check will work, but an encrypted backup contains more information.";
  $("ibPwWrap").classList.toggle("hidden", !b.encrypted);
  $("ibPw").value = "";
  $("ibSelected").scrollIntoView({ behavior: "smooth", block: "center" });
}
$("ibRetryBtn").onclick = $("ibRetry2Btn").onclick = loadBackups;
$("ibSettingsBtn").onclick = () => api("/api/privacy-settings", { post: true });
$("ibPickBtn").onclick = async () => {
  let p = await pick("folder");
  if (!canPick()) p = prompt("Paste the full path of the iPhone backup folder:");
  if (!p) return;
  try { selectBackup(await api("/api/backup-info?path=" + encodeURIComponent(p))); }
  catch (e) { showError(e.message); }
};
$("ibScanBtn").onclick = () => {
  if (state.ios.encrypted && !$("ibPw").value) { $("ibPw").focus(); return showError("Please enter the backup password."); }
  startScan({ kind: "iphone-backup", path: state.ios.path, password: $("ibPw").value });
};

/* ---------- Android ---------- */
document.querySelectorAll("[data-amode]").forEach(b => b.onclick = () => {
  document.querySelectorAll("[data-amode]").forEach(x => x.classList.remove("selected"));
  b.classList.add("selected");
  state.amode = b.dataset.amode; state.apath = null;
  const qf = state.amode === "androidqf";
  $("aHelpBug").classList.toggle("hidden", qf); $("aHelpQf").classList.toggle("hidden", !qf);
  $("aPickFolderBtn").classList.toggle("hidden", !qf || !canPick());
  $("aPickFileBtn").classList.toggle("hidden", !canPick());
  $("aManual").classList.toggle("hidden", canPick());
  $("aPickFileBtn").textContent = qf ? "Choose .zip file…" : "Choose bug report file…";
  $("aPwWrap").classList.toggle("hidden", !qf);
  $("aPicked").classList.add("hidden"); $("aScanBtn").disabled = true;
  $("aSelected").classList.remove("hidden");
});
function setAPath(p) {
  state.apath = p; $("aScanBtn").disabled = !p;
  $("aPicked").classList.toggle("hidden", !p); $("aPickedPath").textContent = p || "";
}
$("aPickFileBtn").onclick = async () => setAPath(await pick("file"));
$("aPickFolderBtn").onclick = async () => setAPath(await pick("folder"));
$("aManual").oninput = (e) => { state.apath = e.target.value.trim(); $("aScanBtn").disabled = !state.apath; };
$("aScanBtn").onclick = () => startScan({
  kind: state.amode === "androidqf" ? "android-androidqf" : "android-bugreport",
  path: state.apath, password: $("aPw").value,
});

/* ---------- running jobs ---------- */
async function startScan(req) {
  hideError();
  try { await api("/api/scan", { body: req }); }
  catch (e) { return showError(e.message); }
  state.lastScan = req;
  $("ibPw").value = ""; $("aPw").value = "";  // don't keep passwords around
  beginRun("scan");
}
const TITLES = { scan: "Checking the phone…", update: "Downloading the latest tools and indicators…" };
function beginRun(kind) {
  state.kind = kind; state.logNext = 0; state.t0 = Date.now();
  setRunning(true);
  if (kind !== "terminal") { $("runTitle").textContent = TITLES[kind]; show("run"); }
  clearInterval(state.poll);
  state.poll = setInterval(pollJob, 700); pollJob();
}
const SPIN = [" ·  ", " ·· ", " ···", "  ··", "   ·", "    "];
async function pollJob() {
  const s = await api("/api/status?since=" + state.logNext).catch(() => null);
  if (!s) return;
  state.logNext = s.next; appendLog(s.lines);
  const ul = $("runSteps"); ul.replaceChildren();
  state.spin = (state.spin + 1) % SPIN.length;
  s.steps.forEach((label, i) => {
    let cls = "";
    if (i < s.step || (i === s.step && !s.running && !s.failed && !s.cancelled)) cls = "done";
    else if (i === s.step && s.running) cls = "active";
    else if (i === s.step && s.failed) cls = "fail";
    const li = el("li", cls, label); li.dataset.spin = SPIN[state.spin]; ul.append(li);
  });
  const sec = Math.round((Date.now() - state.t0) / 1000);
  $("elapsed").textContent = "elapsed " + Math.floor(sec / 60) + "m " + String(sec % 60).padStart(2, "0") + "s";
  if (!s.running) { clearInterval(state.poll); finish(s); }
}
function appendLog(lines) {
  const log = $("log");
  for (const raw of lines) {
    const line = raw.replace(/\x1b\[[0-9;]*m/g, "");
    const cls = line.startsWith("$ ") ? "c" : line.startsWith("=== ") ? "h"
              : /ERROR|CRITICAL|Found a known/.test(line) ? "e" : /WARNING/.test(line) ? "w" : "";
    log.append(el("span", cls, line + "\n"));
  }
  log.scrollTop = log.scrollHeight;
}
function setRunning(on) {
  $("termRun").disabled = on; $("termCmd").disabled = on;
  $("termStop").classList.toggle("hidden", !on);
  $("updateBtn").disabled = on;
}
function renderUpdStatus() {
  const e = state.env;
  $("updStatus").textContent = e.updated
    ? `MVT ${e.mvt_version || "?"} · last updated ${e.updated} · indicators are also refreshed before every check`
    : "indicators are refreshed automatically before every check";
}
async function finish(s) {
  setRunning(false);
  state.env = await api("/api/env");
  state.about = null;  // re-read after updates
  if (s.kind === "terminal") return;
  if (s.kind === "update") {
    if (s.failed) {
      show(state.env.installed ? "home" : "setup");
      showError("The installation didn't finish. Check that you're connected to the internet and try again. " +
                "Someone helping you can look at the Terminal panel.");
      openTerm();
    } else { show("home"); }
    return;
  }
  if (s.cancelled) { show("home"); return; }
  const r = await api("/api/results");
  const v = $("verdict"); show("result");
  $("vAreas").classList.add("hidden"); $("vRetryBtn").classList.add("hidden");
  $("vRevealBtn").classList.toggle("hidden", !r.folder);
  const txt = $("vText"); txt.replaceChildren();
  const p = (t) => txt.append(el("p", null, t));

  if (s.failed && !r.total) {
    v.className = "verdict neutral"; $("vStamp").textContent = "[ INCOMPLETE ]";
    $("vRetryBtn").classList.remove("hidden");
    if (/Unlocking/.test(s.error_step || "")) {
      $("vTitle").textContent = "The backup couldn't be unlocked";
      p("This usually means the password was wrong. Try again with the password you chose when you turned on “Encrypt local backup”.");
    } else {
      $("vTitle").textContent = "The check couldn't be completed";
      p("Something went wrong while checking. Make sure the file or backup is complete and try again. Someone helping you can look at the Terminal panel.");
    }
  } else if (r.total > 0) {
    v.className = "verdict bad"; $("vStamp").textContent = "[ ALERT ] MATCH FOUND";
    $("vTitle").textContent = "Possible signs of spyware were found";
    p(`The check found ${r.total} trace${r.total > 1 ? "s" : ""} matching known spyware indicators. This needs a closer look by an expert. It is not always a confirmed infection.`);
    p("What to do now:");
    const ol = el("ol");
    ["Stay calm. Don't reset or wipe the phone yet. That could erase evidence an expert needs.",
     "Contact a digital-security helpline for free, confidential help, for example Access Now's Digital Security Helpline (accessnow.org/help) or Amnesty International's Security Lab.",
     "Keep the result folder safe (click “Show result files”). The helpers will ask for it.",
     "Until you have advice, assume conversations on this phone may be monitored."].forEach(t => ol.append(el("li", null, t)));
    txt.append(ol);
    $("vAreas").classList.remove("hidden");
    const list = $("vAreaList"); list.replaceChildren();
    r.areas.forEach(a => { const d = el("div", "area"); d.append(el("span", null, a.name), el("b", "evil", a.count)); list.append(d); });
  } else {
    const c = r.counts || {review: 0, minor: 0, info: 0};
    const plural = (n, w) => `${n} ${w} finding${n === 1 ? "" : "s"}`;
    if (c.review) {
      v.className = "verdict warn"; $("vStamp").textContent = "[ REVIEW ] NO KNOWN SPYWARE";
      $("vTitle").textContent = "No known spyware, but something worth a second look";
      p(`Nothing matched known spyware indicators. MVT did mark ${c.review} entr${c.review === 1 ? "y" : "ies"} as unusual (medium level). This is often harmless, but an expert can tell for sure. Each one is explained below.`);
      p("If you're worried, keep the result folder and contact a digital-security helpline such as Access Now's (accessnow.org/help).");
    } else if (c.minor) {
      v.className = "verdict warn"; $("vStamp").textContent = "[ NO MATCHES ] " + plural(c.minor, "MINOR").toUpperCase();
      $("vTitle").textContent = "No known spyware, " + plural(c.minor, "minor") + " to look at";
      p("Nothing matched known indicators of spyware such as Pegasus or Predator. MVT did note some minor things (low level). These are usually harmless, but please read the explanations below and check you recognise them.");
      p("No match is good news, but not a guarantee: new spyware may not be known yet. If you're still worried, contact a digital-security helpline such as Access Now's (accessnow.org/help).");
    } else {
      v.className = "verdict ok"; $("vStamp").textContent = "[ CLEAN ] NO MATCHES";
      $("vTitle").textContent = "No known spyware traces found";
      p("The check found nothing matching the known indicators of spyware such as Pegasus or Predator." +
        (c.info ? ` MVT also recorded ${plural(c.info, "informational")}, explained below.` : ""));
      p("This is good news, but not a guarantee. New spyware may not be known yet, and some traces can be missing from backups. If you're still worried, contact a digital-security helpline such as Access Now's (accessnow.org/help).");
    }
  }
  if (r.total > 0) $("vAreas").querySelector("b").textContent = "Where the traces were found";

  // Every non-spyware finding, with what it is, what to do and the individual entries.
  const findings = r.findings || [];
  $("vFindings").classList.toggle("hidden", !findings.length);
  const fc = $("vFindCount"); fc.replaceChildren();
  const cnt = r.counts || {};
  [["review", "unusual"], ["minor", "minor"], ["info", "informational"]].forEach(([k, w]) => {
    if (cnt[k]) fc.append(el("span", k, `${cnt[k]} ${w}`));
  });
  const fl = $("vFindList"); fl.replaceChildren();
  findings.forEach(f => {
    const d = el("div", "finding " + f.severity);
    const head = el("div", "f-head");
    head.append(el("span", "f-name", `${f.name} (${f.count})`), el("span", "f-badge", f.label));
    d.append(head, el("div", "f-sum" + (f.tone === "good" ? " good" : ""), f.summary));
    const what = el("div", "f-sub"); what.append(el("b", null, "What this is: "), f.what);
    const todo = el("div", "f-sub"); todo.append(el("b", null, "What to do: "), f.todo);
    d.append(what, todo);
    const det = el("details");
    det.append(el("summary", null, `show ${f.count === 1 ? "the entry" : "all " + f.count + " entries"}`));
    const ul = el("ul", "f-entries");
    f.entries.forEach(e => { const li = el("li"); if (e.when) li.append(el("span", "t", e.when)); li.append(e.text); ul.append(li); });
    if (f.more) ul.append(el("li", "muted", `…and ${f.more} more (see “Show result files”)`));
    det.append(ul); d.append(det); fl.append(d);
  });
}

/* ---------- What does it check? ---------- */
// Plain-language meaning of every kind of trace MVT compares (keys = MVT's check_* names).
const KINDS = {
  url:       ["Web addresses & servers", "Spyware is often delivered through a booby-trapped link, and infected phones contact the attacker's servers. These domains are compared with links in text messages, iMessage, WhatsApp, browser history and the phone's network records."],
  process:   ["Hidden spyware programs", "Spyware runs under specific, often harmless-looking process names (e.g. Pegasus used names such as “bh” or “roleaboutd”). These are compared with the program names the phone records in its network-usage, battery, privacy-permission and diagnostic logs."],
  file_path: ["Files left behind", "Paths and names of files that spyware is known to create, compared with the file listing of the backup or collection."],
  file_hash: ["Fingerprints of malicious app files", "SHA-256 fingerprints of known malicious installers (APKs), compared with the files of the apps installed on the phone."],
  app_id:    ["Malicious apps", "Package names of known spyware and stalkerware apps, compared with the list of installed apps and with apps that hold dangerous permissions (accessibility, device admin, SMS…)."],
  app_certificate_hash: ["Signatures of malicious developers", "Every Android app is signed by its developer. Signing certificates used by spyware makers are compared with the certificates of installed apps."],
  email:     ["Attacker accounts", "Email addresses / Apple IDs that attackers used to send invisible exploits via iMessage, FaceTime or calendar invites. They are compared with the accounts the iPhone looked up and with calendar invitations."],
  profile:   ["Malicious configuration profiles", "iOS configuration profiles can silently change settings. Known malicious profile IDs are compared with installed and previously removed profiles."],
  android_property_name: ["Suspicious system properties", "Android system properties known to be set by specific spyware."],
  other:     ["Other traces", "Additional indicator types."],
};
const ALIAS = { urls: "url", domains: "url", file_path_process: "process", files: "file_path" };
const normKind = (k) => ALIAS[k] || (KINDS[k] ? k : "other");

// Short background on well-known families (shown next to the researchers' own data).
const BACKGROUND = {
  Pegasus: "Made by NSO Group (Israel). Infects iPhones and Android phones, often through “zero-click” exploits that need no action from the victim. Exposed by Amnesty's Security Lab in the 2021 Pegasus Project.",
  Predator: "Sold by the Intellexa alliance. Typically delivered through a one-click malicious link sent in a message.",
  OperationTriangulation: "iPhone spyware disclosed by Kaspersky in 2023, delivered through an invisible iMessage attachment (zero-click).",
  KingSpawn: "iPhone spyware from QuaDream (Israel), documented by Citizen Lab and Microsoft in 2023. Delivered via invisible iCloud calendar invitations.",
  Candiru: "Israeli spyware vendor (also known as Saito Tech). Its spyware was documented by Citizen Lab and Microsoft in 2021.",
  RCSLab: "“Hermit” spyware from the Italian vendor RCS Lab, documented in 2022. Victims were tricked into installing an app.",
  Cellebrite: "Forensic phone-unlocking tool used by police with physical access to a phone. Amnesty documented its misuse in Serbia in 2024.",
  NoviSpy: "Android spyware that Amnesty found installed on phones of journalists and activists in Serbia while police held the phones (2024).",
  WyrmSpy_DragonEgg: "Android surveillance apps attributed to the Chinese group APT41 (Lookout, 2023).",
  EagleMsgSpy: "Surveillance tool reportedly used by Chinese police, installed with physical access to a phone (Lookout, 2024).",
  MercenarySpywareCampaign: "Mercenary spyware campaign against Android phones uncovered by Amnesty's Security Lab (2023).",
};

async function loadAbout() {
  if (!state.about) {
    $("aboutLoading").classList.remove("hidden"); $("aboutBody").classList.add("hidden");
    try { state.about = await api("/api/about"); } catch (e) { $("aboutLoading").textContent = e.message; return; }
  }
  $("aboutLoading").classList.add("hidden"); $("aboutBody").classList.remove("hidden");
  const { indicators: ind, modules: mods } = state.about;

  // stats
  const families = ind.sets.reduce((n, s) => n + Math.max(1, s.names.length), 0);
  $("stFam").textContent = fmt(families); $("stIoc").textContent = fmt(ind.total);
  const allMods = new Set(Object.values(mods.scans || {}).flat().map(m => m.name));
  $("stMod").textContent = allMods.size || "–";
  $("stUpd").textContent = (mods.version ? "MVT " + mods.version + " · " : "") +
    (ind.updated ? "indicators downloaded " + ind.updated : "") +
    (!ind.sets.length ? "No indicator files yet. Click “Update everything” on the start screen." : "");

  // threats
  const T = $("threats"); T.replaceChildren();
  for (const s of ind.sets) {
    const d = el("div", "threat");
    const many = s.names.length > 3;
    const title = many ? `Stalkerware & spy apps (${s.names.length} products)` : s.names.join(", ").replace(/_/g, " / ");
    d.append(el("div", "t-name", "⚠ " + title));
    const meta = el("div", "t-meta", [s.publisher, s.date ? "published " + s.date : null, fmt(s.total) + " indicators"].filter(Boolean).join(" · ") + " · ");
    if (s.repo) { const a = el("a", null, "source"); a.href = s.repo; a.target = "_blank"; a.rel = "noopener"; meta.append(a); }
    d.append(meta);
    const bg = !many && BACKGROUND[s.names[0]];
    if (bg) d.append(el("p", null, bg));
    else if (many) d.append(el("p", null, "Commercial “stalkerware” that someone with access to the phone (often a partner or family member) can secretly install. Maintained by Echap."));
    if (s.description && !/^IOCs for/i.test(s.description) && !many) d.append(el("p", "muted", s.description));
    if (many) {
      const det = el("details"); det.append(el("summary", "muted", "show all names"));
      det.append(el("p", "muted", s.names.join(", "))); d.append(det);
    }
    const chips = el("div", "chips");
    Object.entries(s.counts).sort((a, b) => b[1] - a[1]).forEach(([k, n]) => {
      const c = el("span", "chip"); c.append(el("b", null, fmt(n)), " " + KINDS[normKind(k)][0].toLowerCase()); chips.append(c);
    });
    d.append(chips); T.append(d);
  }

  // tabs
  const TABS = { "iphone-backup": "iPhone backup", "iphone-fs": "iPhone full filesystem", "android-bugreport": "Android bug report", "android-androidqf": "AndroidQF" };
  const tabs = $("scanTabs"); tabs.replaceChildren();
  Object.entries(TABS).forEach(([k, label]) => {
    const b = el("button", "btn secondary" + (k === state.aboutScan ? " on" : ""), label);
    b.onclick = () => { state.aboutScan = k; loadAbout(); };
    tabs.append(b);
  });
  const list = (mods.scans || {})[state.aboutScan] || [];

  // methods: per kind of trace, which modules compare it + how many indicators exist
  const iocPerKind = {};
  ind.sets.forEach(s => Object.entries(s.counts).forEach(([k, n]) => { k = normKind(k); iocPerKind[k] = (iocPerKind[k] || 0) + n; }));
  const usedBy = {};
  list.forEach(m => m.checks.forEach(c => { c = normKind(c); (usedBy[c] = usedBy[c] || new Set()).add(m.name); }));
  const M = $("methods"); M.replaceChildren();
  Object.keys(usedBy).sort((a, b) => (iocPerKind[b] || 0) - (iocPerKind[a] || 0)).forEach(k => {
    const d = el("div", "method");
    d.append(el("span", "m-count", fmt(iocPerKind[k] || 0) + " known bad"));
    d.append(el("div", "m-title", KINDS[k][0]));
    d.append(el("p", null, KINDS[k][1]));
    d.append(el("div", "note", "checked by: " + [...usedBy[k]].join(", ")));
    M.append(d);
  });
  if (!list.length) M.append(el("p", "muted", "Install MVT first to see the checks."));

  // module table
  const tb = $("modTable"); tb.replaceChildren();
  list.forEach(m => {
    const tr = el("tr");
    tr.append(el("td", null, m.name));
    const td = el("td"); td.append(el("div", null, m.doc || ""));
    const chips = el("div", "chips");
    if (m.checks.length) [...new Set(m.checks.map(normKind))].forEach(k => chips.append(el("span", "chip", "vs " + KINDS[k][0].toLowerCase())));
    else chips.append(el("span", "chip muted", "context only"));
    td.append(chips); tr.append(td); tb.append(tr);
  });
  $("aboutErr").textContent = (mods.errors || []).join(" · ");
}

/* ---------- buttons ---------- */
$("cancelBtn").onclick = () => api("/api/cancel", { post: true });
$("runTermBtn").onclick = () => openTerm();
$("vHomeBtn").onclick = () => show("home");
$("vRetryBtn").onclick = () => show(state.lastScan && state.lastScan.kind === "iphone-backup" ? "iphone" : "android");
$("vRevealBtn").onclick = () => api("/api/reveal", { body: {} });
$("openResultsBtn").onclick = () => api("/api/reveal", { body: { path: state.env.results_root } });
$("setupBtn").onclick = $("updateBtn").onclick = async () => {
  try { await api("/api/update", { post: true }); beginRun("update"); }
  catch (e) { showError(e.message); }
};

/* ---------- built-in terminal ---------- */
function openTerm() { $("term").classList.remove("hidden"); document.body.classList.add("term-open"); }
function closeTerm() { $("term").classList.add("hidden"); document.body.classList.remove("term-open"); }
$("termToggle").onclick = () => $("term").classList.contains("hidden") ? (openTerm(), $("termCmd").focus()) : closeTerm();
$("termClose").onclick = closeTerm;
$("termClear").onclick = () => $("log").replaceChildren();
$("termStop").onclick = () => api("/api/cancel", { post: true });
$("termForm").onsubmit = async (ev) => {
  ev.preventDefault();
  const cmd = $("termCmd").value.trim();
  if (!cmd) return;
  try { await api("/api/exec", { body: { command: cmd } }); $("termCmd").value = ""; beginRun("terminal"); }
  catch (e) { appendLog(["$ " + cmd, "  " + e.message]); }
};
$("quitBtn").onclick = async () => {
  if (!confirm("Close Easy MVT?")) return;
  if (desktop) return desktop.quit();
  await api("/api/quit", { post: true }).catch(() => {});
  document.body.replaceChildren(el("main", "ok", "> Easy MVT has been closed. You can close this browser tab."));
};

(async function init() {
  state.env = await api("/api/env");
  const s = await api("/api/status?since=0");
  setRunning(false);
  if (s.running) { beginRun(s.kind); return; }
  const h = location.hash.slice(1);
  show(state.env.installed ? (["home", "iphone", "android", "about", "guide"].includes(h) ? h : "home") : "setup");
})();
