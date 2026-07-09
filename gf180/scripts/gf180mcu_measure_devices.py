#!/usr/bin/env python3
"""
gf180mcu_measure_devices.py  —  GF180MCU port of sky130_measure_devices.py
==========================================================================
This is the backend that generates device_db.json for the GF180MCU
Schematic Area Estimator.

It drives the Magic VLSI layout tool in batch mode (magic -dnull -noconsole),
instantiates each gf180mcu_fd_pr device across a parameter sweep, extracts the
physical bounding box of each drawn device, and regresses a *decoupled*
Height/Width model for MOSFETs (plus linear models for resistors, a border
model for MIM caps, and fixed sizes for BJTs).

PORTING NOTE (sky130 -> gf180mcu)
---------------------------------
The original sky130 script had to work around a quirk where the sky130 device
draw procs reference `$sky130::ruleset`, an array variable that does not
resolve correctly from inside those procs when Magic runs in -dnull mode. The
fix was a `string map` that replaced `$sky130::ruleset` with an inline dict
literal in every `sky130::*` proc body.

For gf180mcu the analogous variable is `$gf180mcu::ruleset`. Depending on your
open_pdks / gf180mcu Magic tech version, the gf180mcu draw procs may resolve
fine in -dnull, in which case the patch loop below is a harmless no-op. The RS
ruleset string is a PLACEHOLDER and its values MUST be checked against the
gf180mcu Magic tech (gf180mcuC) before you trust extracted dimensions.
"""

import argparse, json, os, re, subprocess, sys, threading, time
from itertools import groupby
from pathlib import Path
from typing import Any


# ── Locate the gf180mcu_fd_pr MAG library ────────────────────────────────────
def find_mag() -> str:
    for d in [
        os.environ.get("GF180MCU_MAG_DIR", ""),
        os.path.join(os.environ.get("PDK_ROOT", "/usr/share/pdk"),
                     "gf180mcuC/libs.ref/gf180mcu_fd_pr/mag"),
    ]:
        if d and Path(d).exists():
            return d
    return ""

DEFAULT_MAG = find_mag()


# ── Ruleset (PLACEHOLDER — verify against gf180mcuC Magic tech) ───────────────
# These are rough gf180mcu-scale values used only if the gf180mcu draw procs
# need $gf180mcu::ruleset patched in -dnull mode. Confirm before relying on them.
RS = ("poly_surround 0.10 diff_surround 0.07 gate_to_diffcont 0.16 "
      "gate_to_polycont 0.28 gate_extension 0.14 diff_extension 0.30 "
      "contact_size 0.22 via_size 0.26 metal_surround 0.10 sub_surround 0.20 "
      "diff_spacing 0.28 poly_spacing 0.24 diff_poly_space 0.10 "
      "diff_gate_space 0.21 metal_spacing 0.23 mmetal_spacing 0.28 "
      "res_to_cont 0.22 res_diff_spacing 0.22")


# ── MOSFET parameter sweep ───────────────────────────────────────────────────
_base_sweep = [
    (0.22, 0.28, 1), (1.00, 0.28, 1), (1.00, 0.28, 2), (2.00, 0.28, 4),
    (10.0, 10.0, 1), (10.0, 10.0, 4), (10.0, 10.0, 10),
]
_ratio_sweep = []
for _l in [0.28, 0.5, 1.0, 2.0, 5.0]:
    for _ratio in [2, 3, 4, 8]:
        _w = max(0.22, round(_ratio * _l, 2))
        _ratio_sweep.extend([(_w, _l, 1), (_w, _l, 2), (_w, _l, 4)])
MOSFET_SWEEP = sorted(set(_base_sweep + _ratio_sweep))

MOSFET_DEVS = [
    "nfet_03v3", "pfet_03v3",
    "nfet_05v0", "pfet_05v0",
    "nfet_06v0", "pfet_06v0",
    "nfet_03v3_dss", "pfet_03v3_dss",
    "nfet_05v0_dss", "pfet_05v0_dss",
    "nfet_06v0_dss", "pfet_06v0_dss",
    "nfet_06v0_nvt",
]

# ── Poly / plus resistors ────────────────────────────────────────────────────
POLY_RES = [("npolyf_u", None), ("ppolyf_u", None),
            ("nplus_u", None),  ("pplus_u", None)]
RES_L = [2.0, 5.0, 10.0]
RES_W_GEN = [0.5, 1.0, 2.0]

# ── MIM caps ─────────────────────────────────────────────────────────────────
MIM_CAPS = [("cap_mim_2f0_m2m3", 5, 5),
            ("cap_mim_2f0_m2m3", 10, 10),
            ("cap_mim_2f0_m2m3", 20, 20)]

# ── MOS / varactor caps ──────────────────────────────────────────────────────
VAR_CAPS = [("cap_nmos_03v3", 1.0, 1.0),
            ("cap_pmos_03v3", 1.0, 1.0)]

# ── BJTs (fixed-size macros loaded directly from MAG) ────────────────────────
BJTS = {
    "npn_10p00x10p00":          "gf180mcu_fd_pr__npn_10p00x10p00",
    "vertical_pnp_10p00x10p00": "gf180mcu_fd_pr__vertical_pnp_10p00x10p00",
}


# ── TCL builder ──────────────────────────────────────────────────────────────
def build_tcl(mag_dir: str):
    tasks, blocks = [], []
    blocks.append("drc off\ncrashbackups stop\n")

    # Generalized patch: replace $gf180mcu::ruleset with an inline dict literal
    # in every gf180mcu::* proc body that references it. No-op if unused.
    blocks.append(f"""
set __RS_INLINE__ {{[dict create {RS}]}}
foreach __p__ [info procs gf180mcu::*] {{
    catch {{
        set __body__ [info body $__p__]
        if {{[string match "*gf180mcu::ruleset*" $__body__]}} {{
            set __new__ [string map [list {{$gf180mcu::ruleset}} $__RS_INLINE__] $__body__]
            proc $__p__ [info args $__p__] $__new__
        }}
    }}
}}
puts "PATCH_DONE"
""")

    cn = 0
    def meas(tag, dev_ns, param_tcl):
        nonlocal cn; cn += 1
        cell = f"M{cn:04d}"
        return (f"\nset __p__ [{dev_ns}_defaults]\n"
                f"foreach {{__k__ __v__}} {param_tcl} {{ dict set __p__ $__k__ $__v__ }}\n"
                f"cellname create {cell}\nload {cell}\n"
                f"if {{[catch {{{dev_ns}_draw $__p__}} __e__]}} {{\n"
                f"  puts \"MEAS_ERR|{tag}|$__e__\"\n"
                f"}} else {{\n"
                f"  select top cell\n  expand\n"
                f"  puts \"MEAS_START|{tag}\"\n  box\n  puts \"MEAS_END|{tag}\"\n"
                f"}}\n")

    ns = "gf180mcu::gf180mcu_fd_pr__{}"

    for dev in MOSFET_DEVS:
        for (w, l, nf) in MOSFET_SWEEP:
            tag = f"mosfet|{dev}|{w}|{l}|{nf}"
            blocks.append(meas(tag, ns.format(dev), f"{{w {w} l {l} nf {nf} m 1}}"))
            tasks.append({"tag": tag, "cat": "mosfet", "dev": dev,
                          "w": w, "l": l, "nf": nf, "m": 1})

    for (dev, fw) in POLY_RES:
        widths = [fw] if fw else RES_W_GEN
        for w in widths:
            for l in RES_L:
                tag = f"res|{dev}|{w}|{l}"
                blocks.append(meas(tag, ns.format(dev), f"{{w {w} l {l} m 1 nx 1}}"))
                tasks.append({"tag": tag, "cat": "res", "dev": dev, "w": w, "l": l})

    for (dev, w, l) in MIM_CAPS:
        tag = f"mim|{dev}|{w}|{l}"
        blocks.append(meas(tag, ns.format(dev), f"{{w {w} l {l}}}"))
        tasks.append({"tag": tag, "cat": "mim", "dev": dev, "w": w, "l": l})

    for (dev, w, l) in VAR_CAPS:
        tag = f"var|{dev}|{w}|{l}"
        blocks.append(meas(tag, ns.format(dev), f"{{w {w} l {l} nf 1 m 1}}"))
        tasks.append({"tag": tag, "cat": "var", "dev": dev, "w": w, "l": l})

    blocks.append(f"\naddpath {mag_dir}\n")
    for dev_key, mag_cell in BJTS.items():
        tag = f"bjt|{dev_key}"
        blocks.append(f"\nload {mag_cell}\nselect top cell\nexpand\n"
                      f"puts \"MEAS_START|{tag}\"\nbox\nputs \"MEAS_END|{tag}\"\n")
        tasks.append({"tag": tag, "cat": "bjt", "dev": dev_key})

    blocks.append("\nquit -noprompt\n")
    return "".join(blocks), tasks


# ── Box parser ───────────────────────────────────────────────────────────────
BOX_RE = re.compile(
    r'microns:\s+([\d.]+)\s+x\s+([\d.]+)\s+'
    r'\(\s*([\d.-]+),\s+([\d.-]+)\s*\),\s*\(\s*([\d.-]+),\s+([\d.-]+)'
)

def parse(out: str, tasks: list) -> dict:
    results, lines, i = {}, out.splitlines(), 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("MEAS_ERR|"):
            parts = l.split("|", 2)
            print(f"  x {parts[1]} -> {parts[2] if len(parts) > 2 else ''}")
        elif l.startswith("MEAS_START|"):
            tag = l.split("|", 1)[1]
            box_lines = []; i += 1
            while i < len(lines) and not lines[i].startswith("MEAS_END|"):
                box_lines.append(lines[i]); i += 1
            m = BOX_RE.search("\n".join(box_lines))
            if m:
                w, h = float(m.group(1)), float(m.group(2))
                if w > 0.01:
                    results[tag] = {"w_um": round(w, 4), "h_um": round(h, 4),
                                    "area_um2": round(w * h, 4)}
                    print(f"  ok {tag:<55} {w:.3f} x {h:.3f} um")
                else:
                    print(f"  x {tag} -> empty cell {w}x{h}")
            else:
                print(f"  x {tag} -> no bbox")
        i += 1
    return results


# ── DB assembly ──────────────────────────────────────────────────────────────
def build_db(tasks, results):
    db: dict[str, Any] = {"pdk": "gf180mcuC", "units": "micrometers",
                          "mosfets": {d: {"sweep": []} for d in MOSFET_DEVS},
                          "poly_resistors": {}, "mim_caps": {},
                          "var_caps": {}, "bjts": {}}
    ok = 0
    for t in tasks:
        r = results.get(t["tag"])
        if not r:
            continue
        ok += 1; cat, dev = t["cat"], t["dev"]
        entry = {k: v for k, v in t.items() if k not in ("tag", "cat", "dev")}
        entry.update(r)
        if cat == "mosfet":
            db["mosfets"][dev]["sweep"].append(entry)
        elif cat == "res":
            db["poly_resistors"].setdefault(dev, {"sweep": []})["sweep"].append(entry)
        elif cat == "mim":
            db["mim_caps"].setdefault(dev, {"sweep": []})["sweep"].append(entry)
        elif cat == "var":
            db["var_caps"].setdefault(dev, {"sweep": []})["sweep"].append(entry)
        elif cat == "bjt":
            db["bjts"][dev] = r
    print(f"\n{ok}/{len(tasks)} successful")
    return db


# ── Regression ───────────────────────────────────────────────────────────────
def fit_models(db):
    try:
        import numpy as np
    except ImportError:
        print("[skip] numpy not found"); return db

    print("\n-- Fitting MOSFET H/W models --")
    for dev, data in db["mosfets"].items():
        pts = data.get("sweep", [])
        if len(pts) < 3:
            continue
        X_h = np.array([[p["w"], 1.0] for p in pts])
        y_h = np.array([p["h_um"] for p in pts])
        X_w = np.array([[p["l"] * p["nf"], p["nf"], 1.0] for p in pts])
        y_w = np.array([p["w_um"] for p in pts])
        try:
            c_h, *_ = np.linalg.lstsq(X_h, y_h, rcond=None)
            c_w, *_ = np.linalg.lstsq(X_w, y_w, rcond=None)
            ah, bh = [round(float(x), 4) for x in c_h]
            aw, bw, cw = [round(float(x), 4) for x in c_w]
            data["model"] = {"formula": "area=(ah*w+bh)*(aw*l*nf+bw*nf+cw)",
                             "ah": ah, "bh": bh, "aw": aw, "bw": bw, "cw": cw}
            print(f"  {dev:<20} ah={ah} bh={bh} aw={aw} bw={bw} cw={cw}")
        except Exception as e:
            print(f"  [warn] {dev}: {e}")

    print("\n-- Fitting resistor linear models --")
    for dev, data in db["poly_resistors"].items():
        pts = data.get("sweep", [])
        models = []
        for w, grp in groupby(sorted(pts, key=lambda p: p["w"]), key=lambda p: p["w"]):
            grp = list(grp)
            if len(grp) < 2:
                continue
            X = np.array([[p["l"], 1.0] for p in grp])
            y = np.array([p["area_um2"] for p in grp])
            slope, intercept = np.linalg.lstsq(X, y, rcond=None)[0]
            models.append({"w": w, "slope": round(float(slope), 4),
                           "intercept": round(float(intercept), 4)})
        if models:
            data["model"] = models
            print(f"  {dev:<20} {len(models)} width fits")

    print("\n-- MIM border models --")
    for dev, data in db["mim_caps"].items():
        pts = data.get("sweep", [])
        if pts:
            borders = [(p["w_um"] - p["w"]) / 2.0 for p in pts if "w" in p and "w_um" in p]
            if borders:
                b = round(sum(borders) / len(borders), 4)
                data["model"] = {"border_um": b}
                print(f"  {dev:<20} border={b} um")
    return db


# ── Magic runner ─────────────────────────────────────────────────────────────
def run_magic(tcl: str, timeout: int = 900) -> str:
    tcl_path = Path("_gf180mcu_measure.tcl")
    tcl_path.write_text(tcl)
    proc = subprocess.Popen(["magic", "-dnull", "-noconsole", str(tcl_path)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
    out = {"data": ""}
    def reader():
        out["data"] = proc.stdout.read()
    t = threading.Thread(target=reader); t.start()
    t.join(timeout)
    if t.is_alive():
        proc.kill(); t.join()
        print("[warn] magic timed out")
    return out["data"]


def main():
    ap = argparse.ArgumentParser(description="GF180MCU device area DB generator")
    ap.add_argument("--mag-dir", default=DEFAULT_MAG,
                    help="gf180mcu_fd_pr/mag directory (BJT cells)")
    ap.add_argument("--out", default="device_db.json")
    ap.add_argument("--no-fit", action="store_true", help="skip regression")
    args = ap.parse_args()

    if not args.mag_dir:
        print("[warn] Could not auto-locate gf180mcu MAG dir. "
              "Set $PDK_ROOT or $GF180MCU_MAG_DIR, or pass --mag-dir.")

    tcl, tasks = build_tcl(args.mag_dir)
    print(f"Running Magic over {len(tasks)} measurement tasks ...")
    out = run_magic(tcl)
    results = parse(out, tasks)
    db = build_db(tasks, results)
    if not args.no_fit:
        db = fit_models(db)
    Path(args.out).write_text(json.dumps(db, indent=2))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
