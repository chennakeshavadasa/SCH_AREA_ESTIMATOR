#!/usr/bin/env python3
"""
sky130_info.py
──────────────
Run this ONCE. It probes everything needed to write a correct area-estimator:
  • All sky130 draw/defaults proc names and their parameter keys
  • Whether _draw works headless (-dnull) for each device category
  • Exact bounding box for one device per category (proves the method works)
  • Coordinate unit conversion factor
  • Which MAG files exist for fixed-size devices (BJTs etc.)
  • Which bbox extraction method works in TCL

Output: sky130_info.json  (feed this to me, I write the final scripts)

Usage:
    python3 sky130_info.py
    python3 sky130_info.py --magicrc /home/nithin/.ciel/sky130A/libs.tech/magic/sky130A.magicrc
"""

import argparse, json, os, re, subprocess, sys, time, threading
from pathlib import Path

DEFAULT_RC  = "/home/nithin/.ciel/sky130A/libs.tech/magic/sky130A.magicrc"
DEFAULT_MAG = "/home/nithin/.ciel/sky130A/libs.ref/sky130_fd_pr/mag"

# ─────────────────────────────────────────────────────────────────────────────
# BIG DIAGNOSTIC TCL — runs as one Magic session, outputs structured tags
# ─────────────────────────────────────────────────────────────────────────────

TCL = r"""
drc off
crashbackups stop
puts "MAGIC_STARTED"

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: All sky130:: proc names
# ══════════════════════════════════════════════════════════════════════════════
puts "§PROCS_START"
foreach p [lsort [info procs sky130::*]] {
    puts "PROC|$p"
}
puts "§PROCS_END"

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2: Coordinate unit probe
#   Place a 1µm x 1µm rectangle manually; read back box values multiple ways
# ══════════════════════════════════════════════════════════════════════════════
puts "§UNITS_START"
cellname create UNIT_PROBE
load UNIT_PROBE
# Draw a known 1µm x 1µm nwell box (just to establish coordinates)
# In sky130A magic: 1µm = ? internal units?
# We'll read the snap grid to infer
snap microns
box 0 0 1 1
# Try all box subcommands
puts "BOX_PLAIN|[box]"
catch {puts "BOX_LLX|[box llx]"}
catch {puts "BOX_LLY|[box lly]"}
catch {puts "BOX_URX|[box urx]"}
catch {puts "BOX_URY|[box ury]"}
catch {puts "BOX_VALUES|[box values]"}
catch {puts "BOX_WIDTH|[box width]"}
catch {puts "BOX_HEIGHT|[box height]"}
# Print human-readable form too
box
puts "§UNITS_END"

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: _defaults for every device we care about
# ══════════════════════════════════════════════════════════════════════════════
puts "§DEFAULTS_START"
set dev_list {
    sky130_fd_pr__nfet_01v8
    sky130_fd_pr__pfet_01v8
    sky130_fd_pr__nfet_01v8_lvt
    sky130_fd_pr__pfet_01v8_hvt
    sky130_fd_pr__pfet_01v8_lvt
    sky130_fd_pr__nfet_g5v0d16v0
    sky130_fd_pr__pfet_g5v0d16v0
    sky130_fd_pr__nfet_g5v0d10v5
    sky130_fd_pr__pfet_g5v0d10v5
    sky130_fd_pr__res_high_po_0p35
    sky130_fd_pr__res_high_po_0p69
    sky130_fd_pr__res_high_po_1p41
    sky130_fd_pr__res_high_po_2p85
    sky130_fd_pr__res_high_po_5p73
    sky130_fd_pr__res_xhigh_po_0p35
    sky130_fd_pr__res_generic_po
    sky130_fd_pr__res_generic_nd
    sky130_fd_pr__res_generic_pd
    sky130_fd_pr__cap_mim_m3_1
    sky130_fd_pr__cap_mim_m3_2
    sky130_fd_pr__cap_var_lvt
    sky130_fd_pr__cap_var_hvt
    sky130_fd_pr__npn_05v5_W1p00L1p00
    sky130_fd_pr__npn_05v5_W1p00L2p00
    sky130_fd_pr__pnp_05v5_W0p68L0p68
    sky130_fd_pr__pnp_05v5_W3p40L3p40
}
foreach dev $dev_list {
    set proc_def "sky130::${dev}_defaults"
    set proc_draw "sky130::${dev}_draw"
    set has_def  [expr {[llength [info procs $proc_def]]  > 0}]
    set has_draw [expr {[llength [info procs $proc_draw]] > 0}]
    puts "DEV_EXISTS|$dev|has_defaults=$has_def|has_draw=$has_draw"
    if {$has_def} {
        catch {
            set d [$proc_def]
            puts "DEFAULTS|$dev|$d"
        } err
        if {$err ne ""} { puts "DEFAULTS_ERR|$dev|$err" }
    }
}
puts "§DEFAULTS_END"

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: Draw test — one device per category, measure bbox
#   Tests whether _draw actually places geometry in batch (-dnull) mode
# ══════════════════════════════════════════════════════════════════════════════
puts "§DRAW_TEST_START"

proc test_draw {dev params label} {
    set proc_draw "sky130::${dev}_draw"
    set proc_def  "sky130::${dev}_defaults"
    puts "TEST|$label|dev=$dev"

    # Merge defaults with our params
    if {[llength [info procs $proc_def]] > 0} {
        catch { set full_params [$proc_def] } err
        if {$err ne ""} { set full_params {} }
        foreach {k v} $params { dict set full_params $k $v }
    } else {
        set full_params $params
    }
    puts "TEST_PARAMS|$label|$full_params"

    # Create cell
    set cn "DRAW_TEST_${label}"
    cellname create $cn
    load $cn

    # Call draw proc
    if {[llength [info procs $proc_draw]] > 0} {
        if {[catch {$proc_draw $full_params} err]} {
            puts "TEST_DRAW_ERR|$label|$err"
        } else {
            puts "TEST_DRAW_OK|$label"
        }
    } else {
        # Try gencell as fallback
        puts "TEST_NO_DRAW_PROC|$label|trying gencell"
        catch {magic::gencell sky130::$dev $cn $params} err
        puts "TEST_GENCELL|$label|$err"
    }

    # Measure via all available methods
    select top cell
    expand
    # Method A: box subcommands
    catch {puts "TEST_BOX_LLX|$label|[box llx]"}
    catch {puts "TEST_BOX_LLY|$label|[box lly]"}
    catch {puts "TEST_BOX_URX|$label|[box urx]"}
    catch {puts "TEST_BOX_URY|$label|[box ury]"}
    # Method B: human-readable (Python parses microns: line)
    puts "TEST_BOX_START|$label"
    box
    puts "TEST_BOX_END|$label"
}

# MOSFET
test_draw sky130_fd_pr__nfet_01v8 {W 1.0 L 0.15 nf 1 m 1} NFET

# Resistor (try fixed-width one first)
test_draw sky130_fd_pr__res_high_po_0p35 {l 5.0 m 1} RES_HIPOLY

# MIM Cap
test_draw sky130_fd_pr__cap_mim_m3_1 {W 10.0 L 10.0 m 1} MIM_CAP

# BJT
test_draw sky130_fd_pr__npn_05v5_W1p00L1p00 {m 1} NPN_BJT

puts "§DRAW_TEST_END"

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: Load from MAG file — does this work for fixed cells?
# ══════════════════════════════════════════════════════════════════════════════
puts "§MAG_LOAD_START"
set mag_dir "MAG_PATH_PLACEHOLDER"
# Test loading a BJT directly from its .mag file
set bjt_mag "${mag_dir}/sky130_fd_pr__npn_05v5_W1p00L1p00.mag"
if {[file exists $bjt_mag]} {
    puts "MAG_EXISTS|$bjt_mag"
    cellname create MAG_LOAD_TEST
    load MAG_LOAD_TEST
    getcell sky130_fd_pr__npn_05v5_W1p00L1p00
    select top cell
    expand
    puts "MAG_BOX_START"
    box
    puts "MAG_BOX_END"
} else {
    puts "MAG_NOT_FOUND|$bjt_mag"
}
puts "§MAG_LOAD_END"

puts "MAGIC_DONE"
quit -noprompt
"""

# ─────────────────────────────────────────────────────────────────────────────
# PARSER
# ─────────────────────────────────────────────────────────────────────────────

BOX_MICRON_RE = re.compile(
    r'microns:\s+([\d.]+)\s+x\s+([\d.]+)\s+\(\s*([\d.-]+),\s+([\d.-]+)\s*\)'
    r',\s*\(\s*([\d.-]+),\s+([\d.-]+)'
)

def parse_output(out: str, mag_dir: str) -> dict:
    info = {
        "procs":        [],
        "units":        {},
        "devices":      {},
        "draw_tests":   {},
        "mag_load_test": {},
        "raw_sections": {},
    }

    lines = out.splitlines()

    # ── Procs ────────────────────────────────────────────────────────────────
    for l in lines:
        if l.startswith("PROC|"):
            info["procs"].append(l.split("|",1)[1])

    # ── Units probe ──────────────────────────────────────────────────────────
    for l in lines:
        for key in ["BOX_PLAIN","BOX_LLX","BOX_LLY","BOX_URX","BOX_URY",
                    "BOX_VALUES","BOX_WIDTH","BOX_HEIGHT"]:
            if l.startswith(f"{key}|"):
                info["units"][key] = l.split("|",2)[2] if l.count("|")>=2 else l.split("|",1)[1]
    # Get microns line from UNITS section
    in_units = False
    for l in lines:
        if "§UNITS_START" in l: in_units = True
        if "§UNITS_END"   in l: in_units = False
        if in_units:
            m = BOX_MICRON_RE.search(l)
            if m:
                info["units"]["microns_box"] = {
                    "w":float(m.group(1)),"h":float(m.group(2)),
                    "llx":float(m.group(3)),"lly":float(m.group(4)),
                    "urx":float(m.group(5)),"ury":float(m.group(6)),
                }

    # ── Defaults ─────────────────────────────────────────────────────────────
    for l in lines:
        if l.startswith("DEV_EXISTS|"):
            parts = l.split("|")
            dev = parts[1]
            info["devices"][dev] = {
                "has_defaults": "has_defaults=1" in l,
                "has_draw":     "has_draw=1"     in l,
            }
        if l.startswith("DEFAULTS|"):
            parts = l.split("|", 2)
            dev = parts[1]; defs = parts[2]
            if dev in info["devices"]:
                info["devices"][dev]["defaults_raw"] = defs
                # Parse dict: key val key val ...
                try:
                    tokens = defs.split()
                    info["devices"][dev]["defaults"] = {
                        tokens[i]: tokens[i+1]
                        for i in range(0, len(tokens)-1, 2)
                    }
                except:
                    pass
        if l.startswith("DEFAULTS_ERR|"):
            parts = l.split("|",2)
            dev = parts[1]
            if dev in info["devices"]:
                info["devices"][dev]["defaults_error"] = parts[2]

    # ── Draw tests ───────────────────────────────────────────────────────────
    cur_test = None
    in_box   = False
    box_lines= []
    for l in lines:
        if l.startswith("TEST|"):
            parts = l.split("|"); cur_test = parts[1]
            info["draw_tests"][cur_test] = {"dev": parts[2]}
        elif l.startswith("TEST_PARAMS|"):
            parts = l.split("|",2)
            t = parts[1]
            if t in info["draw_tests"]:
                info["draw_tests"][t]["params_used"] = parts[2]
        elif l.startswith("TEST_DRAW_OK|"):
            t = l.split("|")[1]
            if t in info["draw_tests"]: info["draw_tests"][t]["draw_ok"] = True
        elif l.startswith("TEST_DRAW_ERR|"):
            parts = l.split("|",2)
            t = parts[1]
            if t in info["draw_tests"]: info["draw_tests"][t]["draw_error"] = parts[2]
        elif l.startswith("TEST_NO_DRAW_PROC|"):
            t = l.split("|")[1]
            if t in info["draw_tests"]: info["draw_tests"][t]["no_draw_proc"] = True
        elif l.startswith("TEST_BOX_LLX|"):
            parts = l.split("|"); t = parts[1]
            if t in info["draw_tests"]:
                info["draw_tests"][t].setdefault("box_subcommands", {})["llx"] = parts[2]
        elif l.startswith("TEST_BOX_LLY|"):
            parts = l.split("|"); t = parts[1]
            if t in info["draw_tests"]:
                info["draw_tests"][t].setdefault("box_subcommands", {})["lly"] = parts[2]
        elif l.startswith("TEST_BOX_URX|"):
            parts = l.split("|"); t = parts[1]
            if t in info["draw_tests"]:
                info["draw_tests"][t].setdefault("box_subcommands", {})["urx"] = parts[2]
        elif l.startswith("TEST_BOX_URY|"):
            parts = l.split("|"); t = parts[1]
            if t in info["draw_tests"]:
                info["draw_tests"][t].setdefault("box_subcommands", {})["ury"] = parts[2]
        elif l.startswith("TEST_BOX_START|"):
            in_box = True; box_lines = []; cur_test = l.split("|")[1]
        elif l.startswith("TEST_BOX_END|"):
            t = l.split("|")[1]
            in_box = False
            full = "\n".join(box_lines)
            m = BOX_MICRON_RE.search(full)
            if m and t in info["draw_tests"]:
                info["draw_tests"][t]["bbox_um"] = {
                    "w":float(m.group(1)),"h":float(m.group(2)),
                    "llx":float(m.group(3)),"lly":float(m.group(4)),
                    "urx":float(m.group(5)),"ury":float(m.group(6)),
                }
                info["draw_tests"][t]["draw_produced_geometry"] = (
                    float(m.group(1)) > 0.01 and float(m.group(2)) > 0.01
                )
        elif in_box:
            box_lines.append(l)

    # ── MAG file inventory ───────────────────────────────────────────────────
    mag_path = Path(mag_dir)
    if mag_path.exists():
        mag_files = sorted(f.name for f in mag_path.glob("*.mag"))
        info["mag_files"] = mag_files
        info["mag_files_count"] = len(mag_files)
        # Categorize
        info["mag_bjts"]  = [f for f in mag_files if "npn" in f or "pnp" in f]
        info["mag_fets"]  = [f for f in mag_files if "nfet" in f or "pfet" in f]
        info["mag_res"]   = [f for f in mag_files if "res_" in f]
        info["mag_caps"]  = [f for f in mag_files if "cap_" in f]
    else:
        info["mag_files"] = []

    return info


def _spin(stop, start):
    f=["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]; i=0
    while not stop.is_set():
        sys.stdout.write(f"\r  {f[i%10]}  Magic probing...  {time.time()-start:.0f}s  ")
        sys.stdout.flush(); time.sleep(0.12); i+=1
    sys.stdout.write("\r"+" "*50+"\r"); sys.stdout.flush()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--magicrc",  default=DEFAULT_RC)
    p.add_argument("--mag-path", default=DEFAULT_MAG)
    p.add_argument("--magic",    default="magic")
    p.add_argument("--out",      default="sky130_info.json")
    args = p.parse_args()

    if not Path(args.magicrc).exists():
        print(f"[ERROR] Not found: {args.magicrc}"); sys.exit(1)

    # Inject actual MAG path into TCL
    tcl = TCL.replace("MAG_PATH_PLACEHOLDER", args.mag_path)

    cmd = [args.magic, "-rcfile", args.magicrc, "-noconsole", "-dnull"]
    print(f"[RUN] {' '.join(cmd)}\n")
    print("  Probing: proc names, defaults, draw tests (NFET/RES/CAP/BJT), MAG files...\n")

    start = time.time()
    stop  = threading.Event()
    threading.Thread(target=_spin, args=(stop,start), daemon=True).start()

    try:
        res = subprocess.run(cmd, input=tcl, capture_output=True,
                             text=True, timeout=180)
        out = res.stdout + res.stderr
    except subprocess.TimeoutExpired:
        stop.set()
        print("❌ Timeout. Magic hung during probing."); sys.exit(1)
    finally:
        stop.set(); time.sleep(0.2)

    print(f"  Magic exited in {time.time()-start:.1f}s\n")

    if "MAGIC_STARTED" not in out:
        print("❌ Magic didn't start properly. Full output:"); print(out); sys.exit(1)

    # ── Parse ────────────────────────────────────────────────────────────────
    info = parse_output(out, args.mag_path)
    info["magic_version"] = next((l for l in out.splitlines() if "Magic 8" in l), "unknown")
    info["raw_log_tail"]  = "\n".join(out.splitlines()[-40:])  # last 40 lines for debugging

    # ── Save JSON ────────────────────────────────────────────────────────────
    Path(args.out).write_text(json.dumps(info, indent=2))

    # ── Print human summary ──────────────────────────────────────────────────
    print("══════════════════ sky130_info SUMMARY ══════════════════")

    print(f"\n── Procs ({len(info['procs'])} total sky130:: procs)")
    draw_procs     = [p for p in info["procs"] if p.endswith("_draw")]
    default_procs  = [p for p in info["procs"] if p.endswith("_defaults")]
    print(f"   _draw procs    : {len(draw_procs)}")
    print(f"   _defaults procs: {len(default_procs)}")
    print("   Sample _draw procs:")
    for p in draw_procs[:8]: print(f"     {p}")
    if len(draw_procs) > 8: print(f"     ... ({len(draw_procs)-8} more)")

    print(f"\n── Device probe results")
    for dev, data in info["devices"].items():
        has_d = "✔ defaults" if data.get("has_defaults") else "✘ no defaults"
        has_w = "✔ draw" if data.get("has_draw") else "✘ no draw"
        keys  = list(data.get("defaults",{}).keys())
        print(f"   {dev[-30:]:<30}  {has_d}  {has_w}  params={keys}")

    print(f"\n── Draw tests (did _draw produce real geometry in -dnull?)")
    for label, t in info["draw_tests"].items():
        ok   = t.get("draw_produced_geometry", False)
        bbox = t.get("bbox_um", {})
        err  = t.get("draw_error", t.get("no_draw_proc", ""))
        if ok:
            print(f"   {label:<12} ✔  {bbox.get('w',0):.3f} µm × {bbox.get('h',0):.3f} µm")
        else:
            print(f"   {label:<12} ✘  {err or bbox}")

    print(f"\n── Units probe")
    for k,v in info["units"].items():
        print(f"   {k}: {v}")

    print(f"\n── MAG files  ({info.get('mag_files_count',0)} total)")
    print(f"   BJTs : {info.get('mag_bjts',[])}")
    print(f"   FETs : {info.get('mag_fets',[])[:5]} ...")
    print(f"   Res  : {info.get('mag_res',[])[:5]} ...")
    print(f"   Caps : {info.get('mag_caps',[])[:5]} ...")

    print(f"\n[DONE] Full info → {args.out}")
    print(f"       Share sky130_info.json and the area-estimator scripts get written correctly.")
    print("═════════════════════════════════════════════════════════")


if __name__ == "__main__":
    main()
