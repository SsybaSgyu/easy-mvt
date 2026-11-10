"""
Run with the scanner's own Python (.venv) to describe what the installed MVT
actually checks: for every scan type, the modules it runs, each module's own
description, and which kinds of indicators it compares (found by reading the
module's source, including inherited classes). Prints JSON.
"""
import importlib
import inspect
import json
import re
import warnings

warnings.filterwarnings("ignore")

SCANS = {
    "iphone-backup": ["mvt.ios.modules.backup:BACKUP_MODULES", "mvt.ios.modules.mixed:MIXED_MODULES"],
    "iphone-fs": ["mvt.ios.modules.fs:FS_MODULES", "mvt.ios.modules.mixed:MIXED_MODULES"],
    "android-bugreport": ["mvt.android.modules.bugreport:BUGREPORT_MODULES"],
    "android-androidqf": ["mvt.android.modules.androidqf:ANDROIDQF_MODULES"],
}
CHECK_RE = re.compile(r"indicators\.check_([a-z_0-9]+)")


def describe(cls):
    checks = set()
    for k in inspect.getmro(cls):
        if k.__module__.split(".")[0] != "mvt":
            continue
        try:
            checks |= set(CHECK_RE.findall(inspect.getsource(k)))
        except (OSError, TypeError):
            continue
    doc = " ".join((cls.__doc__ or "").split())
    return {"name": cls.__name__, "doc": doc, "checks": sorted(checks)}


out = {"scans": {}, "errors": []}
try:
    import mvt
    from importlib.metadata import version
    out["version"] = version("mvt")
except Exception as e:
    out["errors"].append("mvt not importable: %s" % e)

for scan, refs in SCANS.items():
    mods, seen = [], set()
    for ref in refs:
        path, attr = ref.split(":")
        try:
            for cls in getattr(importlib.import_module(path), attr):
                if cls.__name__ not in seen:
                    seen.add(cls.__name__)
                    mods.append(describe(cls))
        except Exception as e:
            out["errors"].append("%s: %s" % (ref, e))
    out["scans"][scan] = mods

print(json.dumps(out))
