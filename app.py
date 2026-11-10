#!/usr/bin/env python3
"""
Easy MVT - a friendly local interface for Amnesty International's
Mobile Verification Toolkit (https://github.com/mvt-project/mvt).

Unofficial. Standard library only. Wraps the `mvt-ios` / `mvt-android`
command-line tools and installs them itself into a private environment in the
data folder (MVT_UI_DATA, or next to this file when run on its own).

Normally started by the desktop app (electron/main.js); `python3 app.py` opens it in a browser.
"""

import datetime
import glob
import json
import os
import platform
import plistlib
import re
import secrets
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
# Where downloads/settings live. The desktop app points this at the per-user data
# folder (an installed app bundle is read-only); standalone use keeps it next to app.py.
DATA = os.environ.get("MVT_UI_DATA") or HERE
os.makedirs(DATA, exist_ok=True)
WINDOWS = platform.system() == "Windows"
VENV = os.path.join(DATA, ".venv")
VENV_BIN = os.path.join(VENV, "Scripts" if WINDOWS else "bin")
EXE = ".exe" if WINDOWS else ""
HOST = "127.0.0.1"
PORT = int(os.environ.get("MVT_UI_PORT", "8765"))
# The desktop app creates the token and hands it to the page through its preload script, so it
# is never served in the HTML. Standalone browser mode has no such channel and embeds it instead.
TOKEN = os.environ.get("MVT_UI_TOKEN") or secrets.token_urlsafe(24)
TOKEN_IN_HTML = not os.environ.get("MVT_UI_TOKEN")
# Keep MVT's own downloads (spyware indicators, update checks) inside our data folder too.
MVT_DATA = os.path.join(DATA, "mvt-data")
HOME = os.path.expanduser("~")
RESULTS_ROOT = os.path.join(HOME, "Documents", "MVT Results")
IOS_BACKUP_DIRS = [
    os.path.join(HOME, "Library", "Application Support", "MobileSync", "Backup"),          # macOS
    os.path.join(os.environ.get("APPDATA", ""), "Apple Computer", "MobileSync", "Backup"),  # Windows iTunes
    os.path.join(HOME, "Apple", "MobileSync", "Backup"),                                   # Windows (Store/Apple Devices)
]


# --------------------------------------------------------------------------
# Locating / installing MVT
# --------------------------------------------------------------------------
def find_bin(name):
    p = os.path.join(VENV_BIN, name + EXE)
    if os.path.exists(p):
        return p
    return shutil.which(name)


def mvt_installed():
    return bool(find_bin("mvt-ios") and find_bin("mvt-android"))


def find_python():
    """Pick the newest python3 available (MVT needs a recent one)."""
    candidates = ["python3.13", "python3.12", "python3.11", "python3.10", "python3"]
    extra = ["/opt/homebrew/bin", "/usr/local/bin",
             "/Library/Frameworks/Python.framework/Versions/Current/bin"]
    for name in candidates:
        for d in extra:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
        p = shutil.which(name)
        if p:
            return p
    return sys.executable


# --------------------------------------------------------------------------
# Jobs: a sequence of steps, run one after another, output buffered.
# --------------------------------------------------------------------------
class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.reset()

    def reset(self):
        self.kind = None
        self.lines = []
        self.steps = []
        self.step = -1
        self.running = False
        self.failed = False
        self.cancelled = False
        self.error = None
        self.output_dir = None
        self.started = None

    def start(self, kind, steps, output_dir=None, cleanup=None, on_success=None):
        """steps: list of (label, argv, env, allow_fail)."""
        os.makedirs(RESULTS_ROOT, exist_ok=True)  # also the working folder for commands
        with self.lock:
            if self.running:
                raise RuntimeError("Something is already running. Please wait for it to finish.")
            self.reset()
            self.kind = kind
            self.steps = steps
            self.running = True
            self.output_dir = output_dir
            self.started = time.time()
        threading.Thread(target=self._run, args=(cleanup, on_success), daemon=True).start()

    def _log(self, line):
        with self.lock:
            self.lines.append(line)

    def _run(self, cleanup, on_success=None):
        try:
            for i, (label, argv, env, allow_fail) in enumerate(self.steps):
                with self.lock:
                    if self.cancelled:
                        break
                    self.step = i
                self._log("")
                self._log("=== %s ===" % label)
                self._log("$ " + " ".join(argv))
                try:
                    with self.lock:  # cancel() takes this lock too: no process can start after it
                        if self.cancelled:
                            break
                        proc = subprocess.Popen(
                            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, text=True, bufsize=1,
                            errors="replace", cwd=RESULTS_ROOT)
                        self.proc = proc
                except OSError as e:
                    self._log("Could not start: %s" % e)
                    code = 127
                else:
                    for line in proc.stdout:
                        self._log(line.rstrip("\n"))
                    code = proc.wait()
                self._log("[exit code %d]" % code)
                if code != 0 and not allow_fail:
                    with self.lock:
                        self.failed = not self.cancelled
                        self.error = label
                    break
            else:
                if on_success and not self.cancelled:
                    on_success()
        finally:
            if cleanup:
                try:
                    cleanup()
                    self._log("Temporary decrypted data was deleted.")
                except Exception as e:
                    self._log("Cleanup problem: %s" % e)
            with self.lock:
                self.running = False
                self.proc = None

    def cancel(self, wait=0):
        with self.lock:
            self.cancelled = True
            proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()
            if wait:
                try:
                    proc.wait(timeout=wait)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def status(self, since):
        with self.lock:
            return {
                "kind": self.kind,
                "running": self.running,
                "failed": self.failed,
                "cancelled": self.cancelled,
                "error_step": self.error,
                "step": self.step,
                "steps": [s[0] for s in self.steps],
                "lines": self.lines[since:],
                "next": len(self.lines),
                "output_dir": self.output_dir,
            }


JOB = Job()


# --------------------------------------------------------------------------
# Decrypted iPhone backups: unencrypted copies of the whole phone. They must never outlive
# the scan, whether it ends normally, is cancelled, or the app is quit or killed.
# --------------------------------------------------------------------------
TMP_PREFIX = "mvt-decrypted-"
_temp_dirs = set()
_temp_lock = threading.Lock()


def make_temp_dir():
    # mode 0700: only this user can read it. The process ID lets the startup sweep tell
    # a crashed run's leftovers from a copy of the scanner that is still running.
    path = tempfile.mkdtemp(prefix="%s%d-" % (TMP_PREFIX, os.getpid()))
    with _temp_lock:
        _temp_dirs.add(path)
    return path


def remove_temp_dir(path):
    shutil.rmtree(path, ignore_errors=True)
    with _temp_lock:
        _temp_dirs.discard(path)


def remove_all_temp_dirs():
    with _temp_lock:
        paths = list(_temp_dirs)
    for p in paths:
        remove_temp_dir(p)


def _pid_alive(pid):
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)          # signal 0: only checks whether the process exists
    except ProcessLookupError:
        return False
    except OSError:
        return True              # exists but belongs to someone else, or Windows quirks: keep it
    return True


def sweep_leftover_temp_dirs():
    """At startup: delete decrypted copies left behind by a crash or a forced quit."""
    root = tempfile.gettempdir()
    for p in glob.glob(os.path.join(root, TMP_PREFIX + "*")):
        m = re.match(re.escape(TMP_PREFIX) + r"(\d+)-", os.path.basename(p))
        if m and _pid_alive(int(m.group(1))):
            continue             # another running copy of the scanner owns it
        try:
            if os.path.isdir(p) and not os.path.islink(p) and \
               (WINDOWS or os.stat(p).st_uid == os.getuid()):
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def shutdown_and_exit(code=0):
    """Stop the running scan, delete decrypted data, then exit."""
    JOB.cancel(wait=5)
    remove_all_temp_dirs()
    os._exit(code)


def base_env():
    env = dict(os.environ, COLUMNS="160", PYTHONUNBUFFERED="1", NO_COLOR="1",
               MVT_DATA_FOLDER=MVT_DATA)
    env["PATH"] = VENV_BIN + os.pathsep + env.get("PATH", "")
    return env


# --------------------------------------------------------------------------
# Scan plans (the only things the browser can ask for)
# --------------------------------------------------------------------------
def new_results_dir(label):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H.%M")
    path = os.path.join(RESULTS_ROOT, "%s - %s" % (stamp, label))
    os.makedirs(path, exist_ok=True)
    return path


def existing(path, what):
    path = os.path.abspath(os.path.expanduser((path or "").strip()))
    if not path or not os.path.exists(path):
        raise ValueError("We couldn't find the %s you selected. Please choose it again." % what)
    return path


def plan_scan(req):
    if not mvt_installed():
        raise ValueError("MVT isn't installed yet. Please run the one-time setup first.")
    if JOB.running:  # check before creating any folders
        raise RuntimeError("Something is already running. Please wait for it to finish.")
    ios, android = find_bin("mvt-ios"), find_bin("mvt-android")
    kind = req.get("kind")
    env = base_env()
    steps = [("Updating the list of known spyware indicators",
              [ios, "download-iocs"], env, True)]  # offline? keep going with cached ones
    cleanup = None

    if kind == "iphone-backup":
        backup = existing(req.get("path"), "iPhone backup")
        if not os.path.exists(os.path.join(backup, "Manifest.db")) and \
           not os.path.exists(os.path.join(backup, "Manifest.plist")):
            raise ValueError("That folder doesn't look like an iPhone backup "
                             "(no Manifest file inside). Please pick the backup folder itself.")
        out = new_results_dir("iPhone")
        target = backup
        if backup_is_encrypted(backup):
            password = req.get("password") or ""
            if not password:
                raise ValueError("This backup is encrypted. Please enter the backup password.")
            tmp = make_temp_dir()
            denv = dict(env, MVT_IOS_BACKUP_PASSWORD=password)  # env, not argv: hidden from `ps`
            steps.append(("Unlocking the encrypted backup",
                          [ios, "decrypt-backup", "--destination", tmp, backup], denv, False))
            target = tmp
            cleanup = lambda: remove_temp_dir(tmp)
        steps.append(("Scanning the iPhone backup",
                      [ios, "check-backup", "--output", out, target], env, False))

    elif kind == "android-bugreport":
        path = existing(req.get("path"), "bug report file")
        out = new_results_dir("Android bug report")
        steps.append(("Scanning the Android bug report",
                      [android, "check-bugreport", "--output", out, path], env, False))

    elif kind == "android-androidqf":
        path = existing(req.get("path"), "AndroidQF folder")
        out = new_results_dir("Android AndroidQF")
        aenv = dict(env)
        if req.get("password"):
            aenv["MVT_ANDROID_BACKUP_PASSWORD"] = req["password"]
        steps.append(("Scanning the AndroidQF acquisition",
                      [android, "check-androidqf", "--output", out, path], aenv, False))
    else:
        raise ValueError("Unknown scan type.")

    return steps, out, cleanup


STATE_FILE = os.path.join(DATA, ".mvt-ui-state.json")


def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except Exception:
        return {}


TOOLS = os.path.join(DATA, ".tools")
UV = os.environ.get("MVT_UI_UV") or os.path.join(TOOLS, "uv" + EXE)  # the desktop app ships its own uv
PY_WANTED = "3.12"


def uv_env():
    # Keep uv, its downloaded Python and its cache inside the scanner folder.
    return dict(base_env(), UV_PYTHON_INSTALL_DIR=os.path.join(TOOLS, "python"),
                UV_CACHE_DIR=os.path.join(TOOLS, "cache"), UV_NO_PROGRESS="1")


# Pinned uv release and its official SHA-256 checksums (same as scripts/fetch-uv.js).
UV_VERSION = "0.13.7"
UV_SHA256 = {
    "uv-aarch64-apple-darwin": "81b811b57e4624df7ed3aac148e0359100d9962f3d9dce00aec533dc2e086731",
    "uv-x86_64-apple-darwin": "20c47e4c1f630b8abaff8c291fa9b9aa300fef92c4fbff95adf198b55287cda7",
    "uv-x86_64-unknown-linux-gnu": "c5221ff41b7892bfc223543883600125f6a47fe845093e0f5bdff4f359196d83",
    "uv-aarch64-unknown-linux-gnu": "71279f8d3c67241e807b9d8db872d1bf541579fb9f08b7d1eb1682196d1e57dc",
}


def uv_target():
    arch = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}.get(
        platform.machine().lower())
    osname = {"Darwin": "apple-darwin", "Linux": "unknown-linux-gnu"}.get(platform.system())
    if not arch or not osname:
        raise SystemExit("Automatic download of uv isn't supported here. Use the desktop app, "
                         "or install MVT manually: pip install mvt")
    return "uv-%s-%s" % (arch, osname)


def bootstrap():
    """`app.py --bootstrap`: get uv + a modern private Python + a fresh .venv."""
    import hashlib
    import tarfile
    import urllib.request
    os.makedirs(TOOLS, exist_ok=True)
    if not os.path.exists(UV):
        name = uv_target()
        base = "https://github.com/astral-sh/uv/releases/download/%s/%s.tar.gz" % (UV_VERSION, name)
        print("Downloading uv (Python installer by Astral): %s" % base, flush=True)
        data = urllib.request.urlopen(base, timeout=120).read()
        expected = UV_SHA256[name]
        actual = hashlib.sha256(data).hexdigest()
        print("SHA-256 expected %s\n        got      %s" % (expected, actual), flush=True)
        if actual != expected:
            raise SystemExit("Checksum mismatch, refusing to install.")
        tgz = os.path.join(TOOLS, "uv.tar.gz")
        with open(tgz, "wb") as fh:
            fh.write(data)
        with tarfile.open(tgz) as tf:
            member = tf.getmember("%s/uv" % name)
            with tf.extractfile(member) as src, open(UV, "wb") as dst:
                dst.write(src.read())
        os.chmod(UV, 0o755)
        os.remove(tgz)
        print("uv installed.", flush=True)
    vpy = os.path.join(VENV_BIN, "python" + EXE)
    if os.path.exists(vpy):
        out = subprocess.run([vpy, "-c", "import sys; print(sys.version_info >= (3, 10))"],
                             capture_output=True, text=True)
        if out.stdout.strip() == "True":
            print("Private Python environment is up to date.", flush=True)
            return
        print("Existing environment uses an old Python; rebuilding it.", flush=True)
        shutil.rmtree(VENV)
    cmd = [UV, "venv", "--python", PY_WANTED, VENV]
    print("$ " + " ".join(cmd), flush=True)
    subprocess.check_call(cmd, env=uv_env())


def plan_update():
    """Install or upgrade everything (Python, MVT, indicators) in one go."""
    env = uv_env()
    vpy = os.path.join(VENV_BIN, "python" + EXE)
    steps = [
        ("Preparing a private, up-to-date Python",
         [sys.executable, os.path.abspath(__file__), "--bootstrap"], env, False),
        ("Downloading the latest MVT tools (this can take a few minutes)",
         [UV, "pip", "install", "--python", vpy, "--upgrade", "mvt"], env, False),
        ("Downloading the latest spyware indicators",
         [os.path.join(VENV_BIN, "mvt-ios" + EXE), "download-iocs"], env, False),
    ]

    def done():
        try:
            out = subprocess.run([vpy, "-c", "import importlib.metadata as m; print(m.version('mvt'))"],
                                 capture_output=True, text=True, timeout=30)
            version = out.stdout.strip()
        except Exception:
            version = None
        with open(STATE_FILE, "w") as fh:
            json.dump({"mvt_version": version,
                       "updated": datetime.datetime.now().strftime("%d %b %Y, %H:%M")}, fh)
    return steps, done


ALLOWED_TOOLS = ("mvt-ios", "mvt-android")


def plan_terminal(command):
    """Built-in terminal: only MVT's own tools may be run, never a shell."""
    import shlex
    try:
        parts = shlex.split(command)
    except ValueError as e:
        raise ValueError("Couldn't read that command: %s" % e)
    if not parts:
        raise ValueError("Type a command first.")
    if parts[0] not in ALLOWED_TOOLS:
        raise ValueError("For safety, this terminal only runs mvt-ios and mvt-android commands.")
    binary = find_bin(parts[0])
    if not binary:
        raise ValueError("%s isn't installed yet. Click \"Update everything\" first." % parts[0])
    parts = [os.path.expanduser(p) if p.startswith("~") else p for p in parts]
    return [("Terminal: " + command, [binary] + parts[1:], base_env(), False)]


# --------------------------------------------------------------------------
# iPhone backups on this Mac
# --------------------------------------------------------------------------
def backup_is_encrypted(path):
    try:
        with open(os.path.join(path, "Manifest.plist"), "rb") as fh:
            return bool(plistlib.load(fh).get("IsEncrypted"))
    except Exception:
        return False


def backup_info(path):
    info = {"path": path, "name": os.path.basename(path), "date": None,
            "model": None, "encrypted": backup_is_encrypted(path)}
    try:
        with open(os.path.join(path, "Info.plist"), "rb") as fh:
            p = plistlib.load(fh)
        info["name"] = p.get("Device Name") or p.get("Display Name") or info["name"]
        info["model"] = p.get("Product Name") or p.get("Product Type")
        d = p.get("Last Backup Date")
        if isinstance(d, datetime.datetime):
            info["date"] = d.strftime("%d %b %Y, %H:%M")
    except Exception:
        pass
    return info


def list_ios_backups():
    backups, denied = [], False
    for root in IOS_BACKUP_DIRS:
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except PermissionError:
            denied = True
            continue
        backups += [backup_info(os.path.join(root, e)) for e in entries
                    if os.path.isdir(os.path.join(root, e))]
    problem = None if backups else ("permission" if denied else "none")
    return {"backups": backups, "problem": problem}


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------
FRIENDLY = {
    "sms": "Text messages", "safari_history": "Safari browsing history",
    "safari_browserstate": "Safari tabs", "chrome_history": "Chrome history",
    "webkit_session_resource_log": "Web browsing traces", "datausage": "Network data usage",
    "netusage": "Network usage", "shutdownlog": "Shutdown log", "processes": "Running processes",
    "manifest": "Backup file list", "configuration_profiles": "Configuration profiles",
    "calendar": "Calendar", "whatsapp": "WhatsApp", "id_status_cache": "iMessage/FaceTime lookups",
    "os_analytics_ad_daily": "System analytics", "interactionc": "App interactions",
    "packages": "Installed apps", "dumpsys_packages": "Installed apps", "files": "Files",
    "aqf_packages": "Installed apps", "aqf_processes": "Running processes",
    "aqf_files": "Files", "settings": "System settings", "getprop": "System properties",
    "profile_events": "Configuration-profile history", "global_preferences": "System settings (Lockdown Mode)",
    "tcc": "Privacy permissions", "locationd_clients": "Location access", "applications": "Installed apps",
}

# Current MVT writes every alert into *_detected.json, from harmless notes up to
# real indicator matches. Only these count as a possible infection (red):
ALARM_LEVELS = {"HIGH", "CRITICAL"}


def classify(record):
    """'alert' = possible spyware (red), 'review' = MEDIUM, worth an expert look (orange),
    'minor' = LOW, usually harmless but shown (orange), 'info' = INFORMATIONAL (grey)."""
    if not isinstance(record, dict):
        return "alert"
    if record.get("matched_indicator"):
        return "alert"
    level = record.get("level")
    if level is None:
        return "alert"            # older MVT versions only wrote real matches
    if level in ALARM_LEVELS:
        return "alert"
    if level == "MEDIUM":
        return "review"
    if level == "LOW":
        return "minor"
    return "info"                 # INFORMATIONAL and anything unknown below LOW


# What each kind of non-spyware finding means, in plain language: (what it is, what to do).
EXPLAIN = {
    "global_preferences": (
        "MVT reports whether Lockdown Mode is on. Lockdown Mode is Apple's optional extra protection "
        "against targeted spyware.",
        "Nothing to do. If you're at high risk (journalist, activist, lawyer...), consider keeping it on."),
    "profile_events": (
        "Configuration profiles are packages of settings that can be installed on an iPhone, e.g. by a "
        "mobile carrier, a VPN app, an employer/school or an iOS beta. Spyware operators have abused them in "
        "the past, so MVT lists every install or removal.",
        "Check Settings › General › VPN & Device Management. You should recognise every profile there. "
        "Carrier profiles from Apple (com.apple.<carrier>) are normal."),
    "configuration_profiles": (
        "A configuration profile is installed on the phone. Profiles can change network, VPN, certificate or "
        "management settings.",
        "Check Settings › General › VPN & Device Management and remove any profile you don't recognise."),
    "tcc": (
        "These are privacy permissions (camera, microphone, location, contacts...) that apps have been given.",
        "Look through Settings › Privacy & Security and take permissions away from apps that don't need them."),
    "locationd_clients": (
        "These apps or system services have asked for your location.",
        "Check Settings › Privacy & Security › Location Services for apps you don't recognise."),
    "applications": (
        "MVT looked at installed apps and noticed something unusual, e.g. an app not from the App Store.",
        "Make sure you recognise every listed app. Apps from your employer or TestFlight betas are common causes."),
    "shutdownlog": (
        "The shutdown log records processes that were still running when the phone was turned off. "
        "Some spyware has left traces there.",
        "Usually harmless. If you're worried, keep the result folder for an expert."),
    "os_analytics_ad_daily": (
        "System analytics record how much data processes used each day.",
        "Usually harmless. If you're worried, keep the result folder for an expert."),
}
GENERIC_EXPLAIN = (
    "MVT noted this while checking. It is not a match with a known spyware indicator, it's extra context "
    "or a general heuristic.",
    "Usually harmless. If something looks unfamiliar, keep the result folder and ask an expert.")

SEVERITY_LABEL = {"review": "Unusual (medium)", "minor": "Minor (low)", "info": "Information"}


def _when(record):
    t = record.get("event_time") or (record.get("event") or {}).get("timestamp")
    if not t:
        return None
    try:
        return datetime.datetime.fromisoformat(str(t)).strftime("%d %b %Y, %H:%M")
    except ValueError:
        return str(t)


def _carrier(profile_id):
    """'com.apple.TMobile_Germany.<uuid>' -> 'TMobile Germany', else None."""
    parts = str(profile_id or "").split(".")
    if len(parts) >= 3 and parts[0] == "com" and parts[1] == "apple":
        return parts[2].replace("_", " ")
    return None


def entry_text(key, record):
    ev = record.get("event") or {}
    if key == "profile_events" and ev.get("operation"):
        op = {"install": "installed", "remove": "removed"}.get(ev["operation"], ev["operation"])
        carrier = _carrier(ev.get("profile_id"))
        if carrier and "carrier" in str(ev.get("process", "")).lower():
            return "Carrier settings profile %s (%s)" % (op, carrier)
        return 'Profile "%s" %s by %s' % (ev.get("profile_id"), op, ev.get("process"))
    if key == "global_preferences":
        return str(record.get("message", ""))
    msg = str(record.get("message") or "")
    return re.sub(r"^On \S+ \S+ ", "", msg) or json.dumps(ev, default=str)[:300]


def group_summary(key, sev, records):
    """One-line takeaway for a group, tailored where we can tell what happened."""
    n = len(records)
    if key == "global_preferences":
        msgs = " ".join(str(r.get("message", "")) for r in records).lower()
        if "enabled" in msgs:
            return "Lockdown Mode is switched on. This is extra protection against spyware. Good.", "good"
        if "disabled" in msgs:
            return ("Lockdown Mode is off. That's normal. It's an optional extra protection for people at "
                    "high risk.", None)
    if key == "profile_events":
        evs = [r.get("event") or {} for r in records]
        carriers = {_carrier(e.get("profile_id")) for e in evs
                    if "carrier" in str(e.get("process", "")).lower()}
        carriers.discard(None)
        if carriers and all("carrier" in str(e.get("process", "")).lower() for e in evs):
            return ("%d install/removal event%s of your mobile carrier's settings (%s). iOS does this by itself, "
                    "e.g. after a restart, SIM/eSIM change or carrier update. This is normal and not spyware."
                    % (n, "" if n == 1 else "s", ", ".join(sorted(carriers))), "good")
        return ("%d configuration-profile event%s. None matches a known malicious profile, but please make sure "
                "you recognise them (see the entries below)." % (n, "" if n == 1 else "s"), None)
    word = {"review": "unusual", "minor": "minor", "info": "informational"}[sev]
    return ("%d %s entr%s. None matches known spyware." % (n, word, "y" if n == 1 else "ies"), None)


MAX_ENTRIES = 100


def summarize(folder):
    """Sorts MVT's output into real detections (red), things to review / minor findings (orange) and
    information (grey). Everything stays on this computer; the UI only runs on localhost."""
    res = {"folder": folder, "total": 0, "areas": [], "review": [], "findings": [],
           "counts": {"review": 0, "minor": 0, "info": 0}, "scanned": False}
    if not folder or not os.path.isdir(folder):
        return res
    res["scanned"] = bool(glob.glob(os.path.join(folder, "*.json")))
    order = {"review": 0, "minor": 1, "info": 2}
    for path in sorted(glob.glob(os.path.join(folder, "*_detected.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        records = data if isinstance(data, list) else [data]
        if not records:
            continue
        key = os.path.basename(path)[:-len("_detected.json")]
        name = FRIENDLY.get(key, key.replace("_", " ").capitalize())
        groups = {"alert": [], "review": [], "minor": [], "info": []}
        for r in records:
            groups[classify(r)].append(r)
        if groups["alert"]:
            res["total"] += len(groups["alert"])
            res["areas"].append({"key": key, "name": name, "count": len(groups["alert"])})
        if groups["review"]:
            res["review"].append({"key": key, "name": name, "count": len(groups["review"])})
        what, todo = EXPLAIN.get(key, GENERIC_EXPLAIN)
        for sev in ("review", "minor", "info"):
            recs = groups[sev]
            if not recs:
                continue
            summary, tone = group_summary(key, sev, recs)
            res["counts"][sev] += len(recs)
            res["findings"].append({
                "key": key, "name": name, "severity": sev, "label": SEVERITY_LABEL[sev],
                "count": len(recs), "summary": summary, "tone": tone, "what": what, "todo": todo,
                "entries": [{"when": _when(r), "text": entry_text(key, r)} for r in recs[:MAX_ENTRIES]],
                "more": max(0, len(recs) - MAX_ENTRIES)})
    res["findings"].sort(key=lambda f: order[f["severity"]])
    return res


# --------------------------------------------------------------------------
# macOS helpers
# --------------------------------------------------------------------------
def native_pick(kind):
    if platform.system() != "Darwin":
        return None
    what = "folder" if kind == "folder" else "file"
    script = 'POSIX path of (choose %s with prompt "Choose the %s to scan")' % (what, what)
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=900)
        p = out.stdout.strip()
        return p.rstrip("/") if p else None
    except Exception:
        return None


def reveal(path):
    # Only ever open folders inside the results directory.
    os.makedirs(RESULTS_ROOT, exist_ok=True)
    path = os.path.realpath(path or RESULTS_ROOT)
    root = os.path.realpath(RESULTS_ROOT)
    if path != root and not path.startswith(root + os.sep):
        path = root
    if os.path.isdir(path):
        opener = {"Darwin": ["open"], "Windows": ["explorer"]}.get(platform.system(), ["xdg-open"])
        subprocess.Popen(opener + [path])


def open_privacy_settings():
    if platform.system() == "Darwin":
        subprocess.Popen(["open", "x-apple.systempreferences:"
                          "com.apple.preference.security?Privacy_AllFiles"])


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
# Only our own script may run; no inline scripts, no outside connections, no plugins or frames.
CSP = ("default-src 'none'; script-src 'self'; connect-src 'self'; img-src 'self' data:; "
       "style-src 'self' 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Handler(BaseHTTPRequestHandler):
    server_version = "easy-mvt"

    def log_message(self, *a):
        pass

    def _host_ok(self):  # stops DNS-rebinding attacks from other websites
        return self.headers.get("Host", "") in ("%s:%d" % (HOST, PORT), "localhost:%d" % PORT)

    def _auth_ok(self):
        return secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN)

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "bad host"})
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as fh:
                html = fh.read().replace(b"__TOKEN__", TOKEN.encode() if TOKEN_IN_HTML else b"")
            return self._send(200, html, "text/html; charset=utf-8")
        if url.path == "/app.js":  # static code, contains no secrets
            with open(os.path.join(STATIC_DIR, "app.js"), "rb") as fh:
                return self._send(200, fh.read(), "text/javascript; charset=utf-8")
        if not self._auth_ok():
            return self._send(401, {"error": "unauthorized"})
        q = parse_qs(url.query)
        if url.path == "/api/env":
            st = load_state()
            return self._send(200, {"installed": mvt_installed(),
                                    "mvt_version": st.get("mvt_version"),
                                    "updated": st.get("updated"),
                                    "mac": platform.system() == "Darwin",
                                    "platform": platform.system(),
                                    "results_root": RESULTS_ROOT})
        if url.path == "/api/ios-backups":
            return self._send(200, list_ios_backups())
        if url.path == "/api/backup-info":
            p = q.get("path", [""])[0]
            return self._send(200, backup_info(p) if os.path.isdir(p) else {"error": "missing"})
        if url.path == "/api/about":
            return self._send(200, {"indicators": indicators_overview(), "modules": modules_overview()})
        if url.path == "/api/status":
            try:
                since = max(0, int(q.get("since", ["0"])[0]))
            except ValueError:
                return self._send(400, {"error": "bad since"})
            return self._send(200, JOB.status(since))
        if url.path == "/api/results":
            return self._send(200, summarize(JOB.output_dir))
        if url.path == "/api/pick":
            return self._send(200, {"path": native_pick(q.get("kind", ["folder"])[0])})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok() or not self._auth_ok():
            return self._send(403, {"error": "forbidden"})
        path = urlparse(self.path).path
        try:
            if path == "/api/update":
                steps, done = plan_update()
                JOB.start("update", steps, on_success=done)
            elif path == "/api/exec":
                JOB.start("terminal", plan_terminal(self._body().get("command", "")))
            elif path == "/api/scan":
                steps, out, cleanup = plan_scan(self._body())
                try:
                    JOB.start("scan", steps, out, cleanup)
                except RuntimeError:
                    if cleanup:
                        cleanup()
                    try:
                        os.rmdir(out)  # only removes it if still empty
                    except OSError:
                        pass
                    raise
            elif path == "/api/cancel":
                JOB.cancel()
            elif path == "/api/reveal":
                reveal(self._body().get("path") or JOB.output_dir or RESULTS_ROOT)
            elif path == "/api/privacy-settings":
                open_privacy_settings()
            elif path == "/api/quit":
                self._send(200, {"ok": True})
                threading.Thread(target=shutdown_and_exit, daemon=True).start()
                return
            else:
                return self._send(404, {"error": "not found"})
        except (ValueError, RuntimeError) as e:
            return self._send(400, {"error": str(e)})
        return self._send(200, {"ok": True})


def already_running():
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((HOST, PORT)) == 0


# --------------------------------------------------------------------------
# "What does this check?": indicators on the list + how each scan uses them
# --------------------------------------------------------------------------
IOC_DIRS = [os.path.join(MVT_DATA, "indicators")]  # MVT_DATA_FOLDER, works on every OS

# STIX pattern object -> the same category names MVT's check_* methods use
STIX_KIND = {
    "domain-name:value": "url", "url:value": "url", "ipv4-addr:value": "url",
    "process:name": "process", "file:path": "file_path", "file:name": "file_path",
    "file:hashes.sha256": "file_hash", "app:id": "app_id",
    "app:cert.sha1": "app_certificate_hash", "app:cert.sha256": "app_certificate_hash",
    "email-addr:value": "email", "configuration-profile:id": "profile",
    "android-property:name": "android_property_name",
}
_ioc_cache = {}


def indicators_overview():
    import re
    files = []
    for d in IOC_DIRS:
        files += sorted(glob.glob(os.path.join(d, "*.stix2")))
    key = tuple((f, os.path.getmtime(f)) for f in files)
    if _ioc_cache.get("key") == key:
        return _ioc_cache["value"]
    sets, total = [], 0
    pat = re.compile(r"\[([a-z0-9\-]+:[a-zA-Z0-9_.\-']+)\s*=")
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                objs = json.load(fh).get("objects", [])
        except Exception:
            continue
        counts, names, desc = {}, [], ""
        for o in objs:
            if o.get("type") == "malware":
                names.append(o.get("name", "?"))
                desc = desc or o.get("description", "")
            elif o.get("type") == "indicator":
                for p in pat.findall(o.get("pattern", "")):
                    k = STIX_KIND.get(p.replace("'", ""), "other")
                    counts[k] = counts.get(k, 0) + 1
                    total += 1
        base = os.path.basename(f)[:-len(".stix2")]
        parts = base.split("_")
        repo = "https://github.com/%s/%s" % (parts[1], parts[2]) if base.startswith("raw.githubusercontent.com") else None
        date = re.search(r"(20\d\d)[-_](\d\d)(?:[-_](\d\d))?", base)
        sets.append({
            "file": base, "names": sorted(set(names)), "description": desc, "counts": counts,
            "total": sum(counts.values()), "repo": repo,
            "date": "-".join(g for g in date.groups() if g) if date else None,
            "publisher": {"AmnestyTech": "Amnesty International", "mvt-project": "MVT project",
                          "AssoEchap": "Echap (stalkerware research)"}.get(parts[1] if len(parts) > 1 else "", None),
        })
    sets.sort(key=lambda x: x["date"] or "0000", reverse=True)
    value = {"sets": sets, "total": total, "updated": load_state().get("updated")}
    _ioc_cache.update(key=key, value=value)
    return value


_mod_cache = {}


def modules_overview():
    vpy = os.path.join(VENV_BIN, "python" + EXE)
    if not os.path.exists(vpy):
        return {"scans": {}, "errors": ["MVT is not installed yet."]}
    ver = load_state().get("mvt_version")
    if ver and _mod_cache.get("ver") == ver:
        return _mod_cache["value"]
    try:
        out = subprocess.run([vpy, "-W", "ignore", os.path.join(HERE, "introspect.py")],
                             capture_output=True, text=True, timeout=120, cwd=tempfile.gettempdir())
        value = json.loads(out.stdout)
    except Exception as e:
        return {"scans": {}, "errors": [str(e)]}
    _mod_cache.update(ver=ver, value=value)
    return value


def exit_when_parent_closes():
    """Desktop app: the parent keeps our stdin open; EOF means it's gone."""
    def watch():
        try:
            while sys.stdin.read(1024):
                pass
        except Exception:
            pass
        shutdown_and_exit(0)
    threading.Thread(target=watch, daemon=True).start()


def main():
    if "--bootstrap" in sys.argv:
        return bootstrap()
    if os.environ.get("MVT_UI_EMBEDDED"):
        exit_when_parent_closes()
    url = "http://%s:%d/" % (HOST, PORT)
    if already_running():  # second double-click: just bring the window back
        webbrowser.open(url)
        return
    sweep_leftover_temp_dirs()
    for sig in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), lambda *_: shutdown_and_exit(0))
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("Easy MVT running at %s" % url)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    shutdown_and_exit(0)


if __name__ == "__main__":
    main()
