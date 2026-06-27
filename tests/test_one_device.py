#!/usr/bin/env python3
"""test_one_device.py — verify Magic gencell + bbox on one nfet_01v8"""
import argparse, os, re, subprocess, sys, threading, time
from pathlib import Path

# ── Fixes applied ─────────────────────────────────────────────────────────────
# 1. gencell params: literal dict {W 1.0 L 0.15 nf 1 m 1}  (not array name)
# 2. bbox:          parse Magic's printed "microns:" line in Python
#                   (don't try to lindex [box] — it's not a parseable list)
# 3. cell order:    gencell FIRST, load AFTER  (load before = empty cell)
# 4. invocation:    TCL piped via stdin  (positional arg = cell name, not script)
TCL = """\
drc off
crashbackups stop
puts "STEP: gencell"
magic::gencell sky130::sky130_fd_pr__nfet_01v8 MEAS_NFET {W 1.0 L 0.15 nf 1 m 1}
puts "STEP: load"
load MEAS_NFET
puts "STEP: select+expand"
select top cell
expand
puts "STEP: box"
box
puts "STEP: done"
quit -noprompt
"""

# Parse Magic's printed box line:  microns:   W x H  ( llx, lly ), ( urx, ury )
BOX_RE = re.compile(
    r'microns:\s+([\d.]+)\s+x\s+([\d.]+)\s+\(\s*([\d.-]+),\s+([\d.-]+)\s*\)'
    r',\s*\(\s*([\d.-]+),\s+([\d.-]+)'
)

DEFAULT_RC = "/home/nithin/.ciel/sky130A/libs.tech/magic/sky130A.magicrc"

def find_magicrc():
    for c in [os.environ.get("SKY130_MAGICRC",""),
              os.path.join(os.environ.get("PDK_ROOT",""), "sky130A/libs.tech/magic/sky130A.magicrc"),
              DEFAULT_RC]:
        if c and Path(c).exists(): return c
    return ""

def spinner(stop_evt, start):
    f = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
    i = 0
    while not stop_evt.is_set():
        sys.stdout.write(f"\r  {f[i%10]}  Magic running...  {time.time()-start:.0f}s  ")
        sys.stdout.flush(); time.sleep(0.12); i += 1
    sys.stdout.write("\r" + " "*50 + "\r"); sys.stdout.flush()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--magicrc", default="")
    p.add_argument("--magic",   default="magic")
    p.add_argument("--show-tcl", action="store_true")
    p.add_argument("--timeout", type=int, default=120)
    args = p.parse_args()

    if args.show_tcl:
        print(TCL); return

    rc = args.magicrc or find_magicrc()
    if not rc:
        print("[ERROR] sky130A.magicrc not found. Pass --magicrc."); sys.exit(1)

    cmd = [args.magic, "-rcfile", rc, "-noconsole", "-dnull"]
    print(f"[RUN] {' '.join(cmd)}  < <tcl_stdin>\n")

    start = time.time()
    stop  = threading.Event()
    threading.Thread(target=spinner, args=(stop, start), daemon=True).start()

    try:
        res = subprocess.run(cmd, input=TCL, capture_output=True, text=True, timeout=args.timeout)
        out = res.stdout + res.stderr
    except subprocess.TimeoutExpired:
        stop.set()
        print("❌  Timeout — run with --show-tcl and paste into interactive Magic")
        sys.exit(1)
    finally:
        stop.set(); time.sleep(0.2)

    print(f"  exited in {time.time()-start:.1f}s  rc={res.returncode}\n")

    # Show STEP lines and errors
    for line in out.splitlines():
        if any(t in line for t in ["STEP","Error","error","can't","invalid","Warning"]):
            print(f"  magic> {line.rstrip()}")

    m = BOX_RE.search(out)
    if not m:
        print("\n❌  No bbox parsed from Magic output. Full log:\n")
        print(out)
        sys.exit(1)

    w, h = float(m.group(1)), float(m.group(2))
    llx, lly, urx, ury = float(m.group(3)), float(m.group(4)), float(m.group(5)), float(m.group(6))
    print(f"\n✅  SUCCESS")
    print(f"   nfet_01v8  W=1µm L=0.15µm nf=1")
    print(f"   BBox:  {w:.4f} µm  ×  {h:.4f} µm  =  {w*h:.4f} µm²")
    print(f"   Corners: ({llx},{lly}) → ({urx},{ury}) µm")
    print(f"\n   Run full sweep:  python3 sky130_measure_devices.py --out device_db.json")

if __name__ == "__main__":
    main()
