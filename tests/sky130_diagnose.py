#!/usr/bin/env python3
"""
sky130_diagnose.py
──────────────────
Run this FIRST. It discovers every path the measurement scripts need,
verifies each one exists, and prints the exact --flags to use.

Usage:
    python3 sky130_diagnose.py
"""

import os, subprocess, sys, re
from pathlib import Path

W  = "\033[0m"   # reset
G  = "\033[32m"  # green
R  = "\033[31m"  # red
Y  = "\033[33m"  # yellow
B  = "\033[1m"   # bold

def ok(label, value):    print(f"  {G}✔{W}  {B}{label:<35}{W}  {value}")
def fail(label, value):  print(f"  {R}✘{W}  {B}{label:<35}{W}  {value}")
def warn(label, value):  print(f"  {Y}?{W}  {B}{label:<35}{W}  {value}")
def section(title):      print(f"\n{B}── {title} {'─'*(55-len(title))}{W}")

# ─────────────────────────────────────────────────────────────────────────────
section("1. Magic binary")
# ─────────────────────────────────────────────────────────────────────────────

magic_cmd = None
for candidate in ["magic", "/usr/bin/magic", "/usr/local/bin/magic"]:
    try:
        r = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=5)
        version = (r.stdout + r.stderr).strip().splitlines()[0] if (r.stdout + r.stderr).strip() else "unknown"
        ok("magic binary", f"{candidate}  ({version})")
        magic_cmd = candidate
        break
    except (FileNotFoundError, subprocess.TimeoutExpired):
        continue

if not magic_cmd:
    # Try 'which magic'
    r = subprocess.run(["which", "magic"], capture_output=True, text=True)
    if r.returncode == 0:
        magic_cmd = r.stdout.strip()
        ok("magic binary (which)", magic_cmd)
    else:
        fail("magic binary", "NOT FOUND — install magic or add it to PATH")

# ─────────────────────────────────────────────────────────────────────────────
section("2. Environment variables")
# ─────────────────────────────────────────────────────────────────────────────

pdk_root       = os.environ.get("PDK_ROOT", "")
sky130_magicrc = os.environ.get("SKY130_MAGICRC", "")
pdk_path_env   = os.environ.get("PDK_PATH", "")   # sometimes used instead

if pdk_root:      ok("$PDK_ROOT",       pdk_root)
else:             warn("$PDK_ROOT",     "not set")

if sky130_magicrc: ok("$SKY130_MAGICRC", sky130_magicrc)
else:              warn("$SKY130_MAGICRC","not set")

if pdk_path_env:  ok("$PDK_PATH",      pdk_path_env)
else:             warn("$PDK_PATH",     "not set")

# ─────────────────────────────────────────────────────────────────────────────
section("3. Searching for sky130A PDK root directory")
# ─────────────────────────────────────────────────────────────────────────────

# Common locations where ciel / volare / manual installs put the PDK
SEARCH_ROOTS = [
    pdk_root,
    pdk_path_env,
    str(Path.home() / "pdk"),
    str(Path.home() / ".pdk"),
    str(Path.home() / "open_pdks"),
    "/usr/share/pdk",
    "/usr/local/share/pdk",
    "/opt/pdk",
    "/opt/sky130",
    str(Path.home() / ".local/share/pdk"),
]

found_sky130 = []
for root in SEARCH_ROOTS:
    if not root:
        continue
    candidate = Path(root) / "sky130A"
    if candidate.is_dir():
        found_sky130.append(str(candidate))

if found_sky130:
    for p in found_sky130:
        ok("sky130A dir", p)
    sky130_dir = found_sky130[0]
else:
    fail("sky130A dir", "NOT FOUND — check PDK_ROOT or install sky130A")
    sky130_dir = ""

# ─────────────────────────────────────────────────────────────────────────────
section("4. Magic config files inside PDK")
# ─────────────────────────────────────────────────────────────────────────────

magicrc_path   = ""
sky130_tcl     = ""
gencell_tcl    = ""

if sky130_dir:
    magic_tech_dir = Path(sky130_dir) / "libs.tech" / "magic"

    # .magicrc
    rc_candidate = magic_tech_dir / "sky130A.magicrc"
    if rc_candidate.exists():
        ok(".magicrc", str(rc_candidate))
        magicrc_path = str(rc_candidate)
    else:
        fail(".magicrc", f"NOT FOUND at {rc_candidate}")

    # sky130.tcl  (contains gencell procs)
    for name in ["sky130.tcl", "sky130A.tcl"]:
        candidate = magic_tech_dir / name
        if candidate.exists():
            ok("PDK TCL (gencell defs)", str(candidate))
            sky130_tcl = str(candidate)
            break
    if not sky130_tcl:
        fail("PDK TCL", f"NOT FOUND under {magic_tech_dir}")

    # .tech file
    for name in ["sky130A.tech", "sky130.tech"]:
        candidate = magic_tech_dir / name
        if candidate.exists():
            ok(".tech file", str(candidate))
            break
    else:
        warn(".tech file", f"not found under {magic_tech_dir}")

    # List everything in libs.tech/magic for reference
    if magic_tech_dir.exists():
        print(f"\n  Files in {magic_tech_dir}:")
        for f in sorted(magic_tech_dir.iterdir()):
            print(f"    {f.name}")

# ─────────────────────────────────────────────────────────────────────────────
section("5. Probing gencell proc signature in sky130.tcl")
# ─────────────────────────────────────────────────────────────────────────────

if sky130_tcl:
    content = Path(sky130_tcl).read_text(errors="replace")

    # Find all proc definitions related to nfet_01v8 or gencell
    procs = re.findall(r'proc\s+([\w:]+(?:nfet|pfet|gencell|draw|resistor|cap)[^\s]*)\s*\{([^}]*)\}',
                       content, re.IGNORECASE)
    if procs:
        print(f"  Relevant procs found in {Path(sky130_tcl).name}:")
        for name, args in procs[:20]:
            print(f"    proc {name} {{{args.strip()}}}")
    else:
        warn("gencell procs", "Could not auto-detect — check sky130.tcl manually")

    # Also look for 'gencell' keyword usage
    gencell_lines = [l.strip() for l in content.splitlines()
                     if "gencell" in l.lower() and not l.strip().startswith("#")][:10]
    if gencell_lines:
        print(f"\n  'gencell' usage in {Path(sky130_tcl).name}:")
        for l in gencell_lines:
            print(f"    {l}")

# ─────────────────────────────────────────────────────────────────────────────
section("6. PDK device cell files (GDS / MAG)")
# ─────────────────────────────────────────────────────────────────────────────

if sky130_dir:
    # Check for cell files for BJTs (fixed cells)
    bjt_names = [
        "sky130_fd_pr__npn_05v5_W1p00L1p00",
        "sky130_fd_pr__pfet_01v8",
        "sky130_fd_pr__res_high_po_0p35",
        "sky130_fd_pr__cap_mim_m3_1",
    ]

    cell_dirs = [
        Path(sky130_dir) / "libs.ref" / "sky130_fd_pr" / "gds",
        Path(sky130_dir) / "libs.ref" / "sky130_fd_pr" / "mag",
        Path(sky130_dir) / "libs.ref" / "sky130_fd_pr" / "maglef",
        Path(sky130_dir) / "libs.ref" / "sky130_fd_pr" / "lef",
    ]

    for d in cell_dirs:
        if d.exists():
            files = list(d.glob("*.gds")) + list(d.glob("*.mag")) + list(d.glob("*.lef"))
            ok(f"Cell dir ({d.name})", f"{str(d)}  [{len(files)} files]")
        else:
            warn(f"Cell dir ({d.name})", f"not found: {d}")

# ─────────────────────────────────────────────────────────────────────────────
section("7. Python environment")
# ─────────────────────────────────────────────────────────────────────────────

ok("Python", sys.version.split()[0])

try:
    import numpy as np
    ok("numpy", np.__version__ + "  (model fitting available)")
except ImportError:
    warn("numpy", "not installed — model fitting will be skipped  (pip install numpy)")

# ─────────────────────────────────────────────────────────────────────────────
section("8. Summary — copy-paste commands")
# ─────────────────────────────────────────────────────────────────────────────

magic_flag   = f"--magic {magic_cmd}"    if magic_cmd   else "--magic <PATH_TO_MAGIC>"
magicrc_flag = f"--magicrc {magicrc_path}" if magicrc_path else "--magicrc <PATH_TO_sky130A.magicrc>"

print(f"""
  {B}Test one device first:{W}
    python3 test_one_device.py {magicrc_flag} {magic_flag}

  {B}Full sweep:{W}
    python3 sky130_measure_devices.py {magicrc_flag} {magic_flag} --out device_db.json

  {B}Dry-run (print TCL without running Magic):{W}
    python3 sky130_measure_devices.py {magicrc_flag} --dry-run
""")

missing = []
if not magic_cmd:   missing.append("magic binary")
if not magicrc_path: missing.append("sky130A.magicrc")
if not sky130_dir:  missing.append("sky130A PDK directory")

if missing:
    print(f"  {R}{B}⚠  Fix these before running:{W}")
    for m in missing: print(f"  {R}   • {m}{W}")
else:
    print(f"  {G}{B}✔  All required files found. Ready to run.{W}")
