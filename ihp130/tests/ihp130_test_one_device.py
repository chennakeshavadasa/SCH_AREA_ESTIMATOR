#!/usr/bin/env python3
"""
ihp130_test_one_device.py
=========================
Minimal sanity-check that confirms the Magic + IHP130 PDK environment is
working correctly before committing to the full 700+ point measurement sweep.

Instantiates ONE sg13_lv_nmos with default parameters (W=0.6um, L=0.13um, nf=1),
reads its bounding box from Magic, and prints the dimensions.

Run this FIRST to verify:
  1. Magic >= 8.3.573 is installed and on PATH (or MAGIC_EXE is set)
  2. $PDK_ROOT points to the IHP-Open-PDK directory containing ihp-sg13g2/
  3. The magic::gencell API works correctly with your PDK version
  4. Lambda→um conversion is producing plausible numbers (expect ~2um × ~2um)

Usage:
  cd tests
  PDK_ROOT=/path/to/IHP-Open-PDK python3 ihp130_test_one_device.py

  # Or override the Magic executable:
  MAGIC_EXE=/usr/local/bin/magic python3 ihp130_test_one_device.py

  # Override PDK root directly:
  python3 ihp130_test_one_device.py --pdk-root /path/to/ihp-sg13g2
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MAGIC_EXE = os.environ.get("MAGIC_EXE", "magic")
PDK_NAME  = "ihp-sg13g2"
MAGIC_RC  = "libs.tech/magic/ihp-sg13g2.magicrc"


def find_pdk_root() -> Path:
    pdk_env = os.environ.get("PDK_ROOT", "")
    candidates = []
    if pdk_env:
        candidates += [
            Path(pdk_env) / PDK_NAME,
            Path(pdk_env),
        ]
    candidates += [
        Path("/foss/pdks/sg13g2"),
        Path("/foss/pdks/ihp-sg13g2"),
        Path.home() / "pdk" / "ihp-sg13g2",
        Path("/usr/share/pdk/ihp-sg13g2"),
    ]
    for c in candidates:
        if (c / "libs.tech" / "magic" / "ihp-sg13g2.magicrc").exists():
            return c
    sys.exit(
        "[ERROR] Cannot locate ihp-sg13g2 PDK.\n"
        "        Set PDK_ROOT to the parent of the ihp-sg13g2 directory.\n"
        f"        Example: export PDK_ROOT=/path/to/IHP-Open-PDK\n"
        f"        Or pass --pdk-root /path/to/IHP-Open-PDK/ihp-sg13g2"
    )


TEST_TCL = r"""
# ihp130_test_one_device.py — single-device sanity check

source {magicrc}

if {{[tech name] ne "ihp-sg13g2"}} {{
    puts stderr "FATAL: IHP-SG13G2 tech did not load correctly."
    quit -noprompt
}}

puts "INFO: Tech loaded: [tech name]"
puts "INFO: Tech lambda: [tech lambda]"

proc lambda2um {{n}} {{
    set lam [tech lambda]
    set num [lindex $lam 0]
    set den [lindex $lam 1]
    return [expr {{$n * 1.0 * $num / $den}}]
}}

# 1. Initialize sg13g2::ruleset explicitly and fix namespace resolution bugs in the PDK
if {{![info exists ::sg13g2::ruleset]}} {{
    namespace eval ::sg13g2 {{
        variable ruleset [dict create \
            poly_surround    0.07 diff_surround    0.07 gate_to_diffcont 0.19 \
            gate_to_polycont 0.22 gate_extension   0.18 diff_extension   0.18 \
            contact_size     0.16 via_size         0.20 metal_surround   0.05 \
            sub_surround     0.31 diff_spacing     0.21 poly_spacing     0.18 \
            diff_poly_space  0.07 diff_gate_space  0.07 metal_spacing    0.18 \
            mmetal_spacing   0.21 res_to_cont      0.20 res_diff_spacing 0.18 \
        ]
    }}
}}

# Hack to work around PDK procs referencing `$sg13g2::ruleset` without the `::` prefix.
# Tcl resolves relative namespaces from the current namespace.
namespace eval ::sg13g2::sg13g2 {{ variable ruleset $::sg13g2::ruleset }}
namespace eval ::magic::sg13g2 {{ variable ruleset $::sg13g2::ruleset }}

puts "INFO: sg13g2::ruleset namespace workaround applied."

# 2. Load/create an edit cell BEFORE calling gencell.
#    gencell places the device into the current edit cell.
cellname create test_nmos_sanity
load test_nmos_sanity

# 3. Activate box tool and set a placement box at the origin.
#    gencell reads the box lower-left as the insertion point.
tool box
box 0 0 0 0

# ── Instantiate sg13_lv_nmos W=0.6um L=0.13um nf=1 ──────────────────────
# {{*}} expands the flat list into separate key/value args for gencell.
set params {{w 0.6 l 0.13 nf 1 m 1}}
puts "INFO: Creating sg13_lv_nmos with params: $params"

set result [catch {{
    magic::gencell sg13g2::sg13_lv_nmos test_nmos_sanity {{*}}$params
}} err_msg]

if {{$result ne 0}} {{
    puts stderr "ERROR: gencell failed — $err_msg"
    puts stderr "Check your PDK version and Magic version (need >= 8.3.573)"
    quit -noprompt
}}

# gencell already loaded the cell; re-select and expand to get full bbox
load test_nmos_sanity
select top cell
expand

puts "RESULT_READY"
box

puts "        lambda_scale: [tech lambda]"

quit -noprompt
"""


def main():
    parser = argparse.ArgumentParser(
        description="Minimal Magic+IHP130 sanity check — tests one sg13_lv_nmos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--pdk-root", default=None,
        help="Path to the ihp-sg13g2 PDK root directory.")
    parser.add_argument(
        "--timeout", type=int, default=120,
        help="Magic timeout in seconds (default: 120).")
    args = parser.parse_args()

    # Locate PDK
    if args.pdk_root:
        pdk_root = Path(args.pdk_root)
    else:
        pdk_root = find_pdk_root()

    magicrc = pdk_root / MAGIC_RC
    if not magicrc.exists():
        sys.exit(f"[ERROR] Magic RC not found: {magicrc}")

    print(f"PDK root: {pdk_root}")
    print(f"Magic RC: {magicrc}")
    print(f"Magic   : {MAGIC_EXE}")
    print()

    # Generate TCL script
    tcl = TEST_TCL.format(magicrc=str(magicrc))

    with tempfile.NamedTemporaryFile(mode="w", suffix=".tcl", delete=False,
                                     prefix="ihp130_sanity_") as tf:
        tf.write(tcl)
        tcl_path = tf.name

    cmd = [MAGIC_EXE, "-dnull", "-noconsole", "-norcfile", tcl_path]
    print(f"Running: {' '.join(cmd)}\n{'─'*60}", flush=True)

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    try:
        # stdin=DEVNULL prevents Magic from blocking on terminal input.
        # Stream stdout live so progress is visible immediately.
        import threading, queue as _queue
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def _drain(stream, store):
            for line in stream:
                line = line.rstrip("\n")
                store.append(line)
                print(f"  {line}", flush=True)

        t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_lines), daemon=True)
        t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_lines), daemon=True)
        t_out.start(); t_err.start()

        try:
            proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            os.unlink(tcl_path)
            sys.exit(f"[ERROR] Magic timed out after {args.timeout}s")

        t_out.join(timeout=5)
        t_err.join(timeout=5)

    except FileNotFoundError:
        sys.exit(
            f"[ERROR] Magic not found: '{MAGIC_EXE}'\n"
            f"        Install Magic >= 8.3.573 or set MAGIC_EXE env var."
        )
    finally:
        try:
            os.unlink(tcl_path)
        except OSError:
            pass

    stdout_text = "\n".join(stdout_lines)

    if stderr_lines:
        print("\n[STDERR]:")
        for line in stderr_lines:
            print(f"  {line}")

    # Parse result
    m = re.search(
        r"RESULT_READY.*?microns:\s+([0-9.eE+\-]+)\s+x\s+([0-9.eE+\-]+)",
        stdout_text,
        re.DOTALL
    )

    print(f"\n{'─'*60}")
    if m:
        w_um, h_um = float(m.group(1)), float(m.group(2))
        area_um2 = w_um * h_um
        print(f"✓  PASSED  —  sg13_lv_nmos  W=0.6um  L=0.13um  nf=1")
        print(f"   Physical bounding box: {w_um:.3f} µm × {h_um:.3f} µm")
        print(f"   Area:                  {area_um2:.3f} µm²")

        # Sanity check: for a 130nm NMOS with W=0.6, L=0.13, the layout
        # should be roughly 2–4 µm in each dimension.
        if 1.0 < w_um < 8.0 and 1.0 < h_um < 8.0:
            print(f"\n   Dimensions look plausible for a 130nm device. ✓")
            print(f"   You can now run: scripts/ihp130_measure_devices.py")
        else:
            print(f"\n   [WARN] Dimensions outside the expected 1–8 µm range.")
            print(f"          Lambda→µm conversion may need adjustment.")
            print(f"          Check 'tech lambda' output above and verify the scale.")
    else:
        print("✗  FAILED  —  No RESULT line found in Magic output.")
        print("   Check the STDERR section above for error details.")
        print(f"\n   Common causes:")
        print(f"     • Magic version < 8.3.573 (needs PCells for IHP130)")
        print(f"     • Corrupted PDK installation")
        print(f"     • 'gencell sg13g2::sg13_lv_nmos' fails silently")
        print(f"\n   Try opening Magic interactively and running:")
        print(f"     magic -d XR -rcfile {magicrc}")
        print(f"     (then) magic::gencell sg13g2::sg13_lv_nmos")
        sys.exit(1)


if __name__ == "__main__":
    main()
