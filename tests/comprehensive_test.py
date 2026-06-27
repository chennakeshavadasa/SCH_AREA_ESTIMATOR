#!/usr/bin/env python3
"""
Try every possible approach to get _draw working.
Prints exactly what worked so the full script can be written correctly.
"""
import subprocess, sys, re, time, threading
from pathlib import Path

RC      = "/home/nithin/.ciel/sky130A/libs.tech/magic/sky130A.magicrc"
MAG_DIR = "/home/nithin/.ciel/sky130A/libs.ref/sky130_fd_pr/mag"

RS = "dict create poly_surround 0.08 diff_surround 0.06 gate_to_diffcont 0.145 gate_to_polycont 0.275 gate_extension 0.13 diff_extension 0.29 contact_size 0.17 via_size 0.17 metal_surround 0.08 sub_surround 0.18 diff_spacing 0.28 poly_spacing 0.21 diff_poly_space 0.075 diff_gate_space 0.20 metal_spacing 0.23 mmetal_spacing 0.14 res_to_cont 0.20 res_diff_spacing 0.20"

# nfet_01v8 device-specific dict (from read_ruleset.txt proc bodies)
NFET_DICT = "dict create gate_type nfet diff_type ndiff diff_contact_type ndc plus_diff_type psd plus_contact_type psc poly_type poly poly_contact_type pc sub_type psub"

TCL = f"""
drc off
crashbackups stop
puts "=START="

# ── DIAGNOSTIC A: print actual proc body of nfet_01v8_draw ───────────────
puts "=PROC_BODY_START="
puts [info body sky130::sky130_fd_pr__nfet_01v8_draw]
puts "=PROC_BODY_END="

# ── DIAGNOSTIC B: can a user proc read sky130::ruleset? ──────────────────
namespace eval sky130 {{ set ruleset [{RS}] }}
proc ::test_read_ruleset {{}} {{
    if {{[catch {{set x $sky130::ruleset}} err]}} {{
        puts "USER_PROC_READ: FAIL $err"
    }} else {{
        puts "USER_PROC_READ: OK size=[dict size $x]"
    }}
}}
test_read_ruleset

# ── APPROACH 1: variable declaration inside namespace eval ────────────────
namespace eval sky130 {{ variable ruleset [{RS}] }}
set p [sky130::sky130_fd_pr__nfet_01v8_defaults]
dict set p w 1.0 ; dict set p l 0.15 ; dict set p nf 1 ; dict set p m 1
cellname create MEAS1 ; load MEAS1
if {{[catch {{sky130::sky130_fd_pr__nfet_01v8_draw $p}} e]}} {{
    puts "A1_FAIL: $e"
}} else {{ select top cell; expand; puts "A1_BOX_START"; box; puts "A1_BOX_END" }}

# ── APPROACH 2: monkey-patch draw proc to inject variable ruleset ─────────
rename sky130::sky130_fd_pr__nfet_01v8_draw sky130::sky130_fd_pr__nfet_01v8_draw_orig
proc sky130::sky130_fd_pr__nfet_01v8_draw {{parameters}} {{
    variable ruleset
    if {{![info exists ruleset]}} {{
        set ruleset [{RS}]
    }}
    sky130::sky130_fd_pr__nfet_01v8_draw_orig $parameters
}}
cellname create MEAS2 ; load MEAS2
if {{[catch {{sky130::sky130_fd_pr__nfet_01v8_draw $p}} e]}} {{
    puts "A2_FAIL: $e"
}} else {{ select top cell; expand; puts "A2_BOX_START"; box; puts "A2_BOX_END" }}

# ── APPROACH 3: call mos_draw directly (bypass _draw, merge dict manually) ─
set ruleset_d [{RS}]
set dev_d [{NFET_DICT}]
set p2 [sky130::sky130_fd_pr__nfet_01v8_defaults]
dict set p2 w 1.0 ; dict set p2 l 0.15 ; dict set p2 nf 1 ; dict set p2 m 1
set full_d [dict merge $ruleset_d $dev_d $p2]
# also inject ruleset into the dict so mos_draw can find it if needed
dict set full_d ruleset $ruleset_d
cellname create MEAS3 ; load MEAS3
if {{[catch {{sky130::mos_draw $full_d}} e]}} {{
    puts "A3_FAIL: $e"
}} else {{ select top cell; expand; puts "A3_BOX_START"; box; puts "A3_BOX_END" }}

# ── APPROACH 4: patch sky130::ruleset as global + upvar ──────────────────
set ::__ruleset__ [{RS}]
namespace eval sky130 {{ upvar #0 ::__ruleset__ ruleset }}
cellname create MEAS4 ; load MEAS4
rename sky130::sky130_fd_pr__nfet_01v8_draw_orig sky130::sky130_fd_pr__nfet_01v8_draw
if {{[catch {{sky130::sky130_fd_pr__nfet_01v8_draw $p}} e]}} {{
    puts "A4_FAIL: $e"
}} else {{ select top cell; expand; puts "A4_BOX_START"; box; puts "A4_BOX_END" }}

# ── BJTs via rf_ MAG files (confirmed working) ───────────────────────────
addpath {MAG_DIR}
foreach {{tag cell}} {{
    npn_W1L1  sky130_fd_pr__rf_npn_05v5_W1p00L1p00
    npn_W1L2  sky130_fd_pr__rf_npn_05v5_W1p00L2p00
    pnp_W068  sky130_fd_pr__rf_pnp_05v5_W0p68L0p68
    pnp_W340  sky130_fd_pr__rf_pnp_05v5_W3p40L3p40
}} {{
    load $cell
    select top cell ; expand
    set b [box values]
    set w [expr {{([lindex $b 2]-[lindex $b 0])/200.0}}]
    set h [expr {{([lindex $b 3]-[lindex $b 1])/200.0}}]
    puts "BJT|$tag|$w|$h"
}}

puts "=END="
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
                         input=TCL, capture_output=True, text=True, timeout=90)
    out = res.stdout + res.stderr
finally:
    stop.set(); time.sleep(0.2)

print(f"Magic rc={res.returncode}\n")

# ── Print proc body (first 20 lines) ─────────────────────────────────────
lines = out.splitlines()
try:
    s = next(i for i,l in enumerate(lines) if "=PROC_BODY_START=" in l)
    e = next(i for i,l in enumerate(lines) if "=PROC_BODY_END=" in l)
    body_lines = lines[s+1:e]
    print("── Actual proc body of nfet_01v8_draw ─────────────────────────")
    for l in body_lines[:25]:
        print(f"  {l}")
    if len(body_lines) > 25:
        print(f"  ... ({len(body_lines)-25} more lines)")
    print()
except StopIteration:
    print("  (proc body not found in output)")

# ── Print all diagnostic/result lines ────────────────────────────────────
SHOW = ["USER_PROC","A1_","A2_","A3_","A4_","BJT|","FAIL","can't","Error"]
print("── Approach results ────────────────────────────────────────────")
for line in lines:
    if any(t in line for t in SHOW):
        print(f"  {line.rstrip()}")

# ── Parse bboxes for each approach ───────────────────────────────────────
print("\n── Bbox results ────────────────────────────────────────────────")
for label, s_tag, e_tag in [
    ("A1 (variable decl)",    "A1_BOX_START","A1_BOX_END"),
    ("A2 (monkey-patch)",     "A2_BOX_START","A2_BOX_END"),
    ("A3 (mos_draw direct)",  "A3_BOX_START","A3_BOX_END"),
    ("A4 (upvar)",            "A4_BOX_START","A4_BOX_END"),
]:
    try:
        s = next(i for i,l in enumerate(lines) if s_tag in l)
        e = next(i for i,l in enumerate(lines) if e_tag   in l)
        m = BOX_RE.search("\n".join(lines[s:e]))
        if m:
            w,h = float(m.group(1)), float(m.group(2))
            ok = w > 0.01
            print(f"  {'✅' if ok else '✘ '}  {label}: {w:.4f} µm × {h:.4f} µm = {w*h:.4f} µm²")
        else:
            print(f"  ✘   {label}: no microns line")
    except StopIteration:
        print(f"  ✘   {label}: tags not found")

# ── BJTs ─────────────────────────────────────────────────────────────────
print("\n── BJT results ─────────────────────────────────────────────────")
for line in lines:
    if line.startswith("BJT|"):
        parts = line.split("|")
        tag, w, h = parts[1], float(parts[2]), float(parts[3])
        print(f"  {'✅' if float(w)>0.01 else '✘ '}  {tag}: {w} µm × {h} µm")
