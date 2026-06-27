#!/usr/bin/env python3
"""Find the PDK proc that initializes sky130::ruleset, call it, then draw."""
import subprocess, sys, re, time, threading
from pathlib import Path

RC      = "/home/nithin/.ciel/sky130A/libs.tech/magic/sky130A.magicrc"
MAG_DIR = "/home/nithin/.ciel/sky130A/libs.ref/sky130_fd_pr/mag"

TCL = f"""
drc off
crashbackups stop

# ── Step 1: find which proc body contains "set ruleset" + "dict create" ──
puts "=== SEARCHING FOR RULESET INIT PROC ==="
foreach p [lsort [info procs sky130::*]] {{
    set body [info body $p]
    if {{[string match "*set ruleset*" $body] || [string match "*variable ruleset*" $body]}} {{
        puts "CANDIDATE: $p"
        # show first 3 lines of body
        set lines [split $body "\\n"]
        foreach l [lrange $lines 0 4] {{ puts "  BODY: $l" }}
    }}
}}
puts "=== END SEARCH ==="

# ── Step 2: call it and verify ────────────────────────────────────────────
# Try common names for the tech init proc
foreach candidate {{
    sky130::tech_setup
    sky130::setup
    sky130::sky130_tech_setup
    sky130::init_ruleset
    sky130::setup_rules
}} {{
    if {{[llength [info procs $candidate]] > 0}} {{
        puts "CALLING: $candidate"
        catch {{$candidate}} err
        puts "RESULT: $err"
        puts "RULESET_EXISTS: [info exists sky130::ruleset]"
        break
    }}
}}

# ── Step 3: try draw now ──────────────────────────────────────────────────
set p [sky130::sky130_fd_pr__nfet_01v8_defaults]
dict set p w 1.0
dict set p l 0.15
dict set p nf 1
dict set p m 1
cellname create MEAS_NFET
load MEAS_NFET
if {{[catch {{sky130::sky130_fd_pr__nfet_01v8_draw $p}} err]}} {{
    puts "DRAW_ERR: $err"
}} else {{
    select top cell
    expand
    puts "NFET_BOX_START"
    box
    puts "NFET_BOX_END"
}}

# ── Step 4: BJT — try rf_ prefix (actual filename has rf_ in MAG dir) ────
addpath {MAG_DIR}
foreach bjt_name {{
    sky130_fd_pr__npn_05v5_W1p00L1p00
    sky130_fd_pr__rf_npn_05v5_W1p00L1p00
}} {{
    cellname create MEAS_BJT
    load $bjt_name
    select top cell
    expand
    set b [box values]
    set w [expr {{([lindex $b 2]-[lindex $b 0])/200.0}}]
    set h [expr {{([lindex $b 3]-[lindex $b 1])/200.0}}]
    if {{$w > 0.01}} {{
        puts "BJT_OK|$bjt_name|$w|$h"
        break
    }} else {{
        puts "BJT_EMPTY|$bjt_name"
    }}
}}

quit -noprompt
"""

BOX_RE = re.compile(
    r'microns:\s+([\d.]+)\s+x\s+([\d.]+)\s+\(\s*([\d.-]+),\s+([\d.-]+)\s*\),\s*\(\s*([\d.-]+),\s+([\d.-]+)'
)

def spin(stop, start):
    f=["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]; i=0
    while not stop.is_set():
        sys.stdout.write(f"\r  {f[i%10]}  {time.time()-start:.0f}s  ")
        sys.stdout.flush(); time.sleep(0.12); i+=1
    sys.stdout.write("\r"+" "*30+"\r"); sys.stdout.flush()

stop = threading.Event()
threading.Thread(target=spin, args=(stop, time.time()), daemon=True).start()
try:
    res = subprocess.run(["magic","-rcfile",RC,"-noconsole","-dnull"],
                         input=TCL, capture_output=True, text=True, timeout=60)
    out = res.stdout + res.stderr
finally:
    stop.set(); time.sleep(0.2)

print(f"Magic rc={res.returncode}\n")

SHOW = ["CANDIDATE","BODY","CALLING","RESULT","RULESET","DRAW","NFET","BJT",
        "microns","can't","Error","error"]
for line in out.splitlines():
    if any(t in line for t in SHOW):
        print(f"  {line.rstrip()}")

# Parse NFET bbox
ls = out.splitlines()
try:
    s = next(i for i,l in enumerate(ls) if "NFET_BOX_START" in l)
    e = next(i for i,l in enumerate(ls) if "NFET_BOX_END"   in l)
    m = BOX_RE.search("\n".join(ls[s:e]))
    if m:
        w,h = float(m.group(1)), float(m.group(2))
        print(f"\n  {'✅' if w>0.01 else '✘ '}  NFET: {w:.3f} µm × {h:.3f} µm = {w*h:.3f} µm²")
    else:
        print(f"\n  ✘   NFET: no microns line")
except StopIteration:
    print(f"\n  ✘   NFET: box tags not found")
