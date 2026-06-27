#!/usr/bin/env python3
"""Probe sky130A.tcl to find _draw and _defaults proc names and signatures"""
import subprocess, sys

RC = "/home/nithin/.ciel/sky130A/libs.tech/magic/sky130A.magicrc"

TCL = """\
drc off
crashbackups stop

# ── List all sky130:: procs containing nfet or 01v8 ──────────────────────
puts "=== PROCS ==="
foreach p [lsort [info procs sky130::*]] {
    if {[string match *nfet* $p] || [string match *01v8* $p] || \
        [string match *_draw* $p] || [string match *_defaults* $p]} {
        puts "PROC: $p"
    }
}
puts "=== END PROCS ==="

# ── Try _defaults to see param structure ─────────────────────────────────
if {[llength [info procs sky130::sky130_fd_pr__nfet_01v8_defaults]] > 0} {
    puts "=== DEFAULTS ==="
    set d [sky130::sky130_fd_pr__nfet_01v8_defaults]
    puts "DEFAULTS: $d"
    puts "=== END DEFAULTS ==="
} else {
    puts "NO _defaults proc found"
}

# ── Try drawing directly ──────────────────────────────────────────────────
cellname create PROBE_NFET
load PROBE_NFET

if {[llength [info procs sky130::sky130_fd_pr__nfet_01v8_defaults]] > 0} {
    set params [sky130::sky130_fd_pr__nfet_01v8_defaults]
    dict set params W 1.0
    dict set params L 0.15
    dict set params nf 1
    dict set params m 1
    puts "DRAWING with params: $params"
    if {[catch {sky130::sky130_fd_pr__nfet_01v8_draw $params} err]} {
        puts "DRAW_ERROR: $err"
    } else {
        puts "DRAW_OK"
    }
}

select top cell
expand
puts "=== BOX ==="
box
puts "=== END BOX ==="
quit -noprompt
"""

cmd = ["magic", "-rcfile", RC, "-noconsole", "-dnull"]
res = subprocess.run(cmd, input=TCL, capture_output=True, text=True, timeout=60)
out = res.stdout + res.stderr

# Print everything relevant
for line in out.splitlines():
    if any(t in line for t in ["PROC:","DEFAULTS:","DRAW","BOX","microns","Error","error","can't"]):
        print(line)
