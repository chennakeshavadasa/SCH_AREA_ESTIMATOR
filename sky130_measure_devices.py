#!/usr/bin/env python3
"""
sky130_measure_devices.py  —  final, working version
─────────────────────────────────────────────────────
Root cause of all previous failures:
    magic::_draw procs do  set drawdict [dict merge $sky130::ruleset ...]
    but $sky130::ruleset is unresolvable from inside those procs in -dnull mode,
    even though the variable exists (info exists returns 1).

Fix: patch every sky130::* proc body with string map, replacing
    $sky130::ruleset  →  [dict create poly_surround 0.08 ...]  (inline literal)
This was confirmed working by the comprehensive_test.py A3 approach.

BJTs: load directly from rf_ MAG files (confirmed: npn 8.62µm, pnp 3.98/6.7µm)
"""

import argparse, json, os, re, subprocess, sys, threading, time
from itertools import groupby
from pathlib import Path
from typing import Any

def find_mag():
    for d in [os.environ.get("SKY130_MAG_DIR",""),
              os.path.join(os.environ.get("PDK_ROOT","/usr/share/pdk"),"sky130A/libs.ref/sky130_fd_pr/mag"),
              "/home/nithin/.ciel/sky130A/libs.ref/sky130_fd_pr/mag"]:
        if d and Path(d).exists(): return d
    return ""

DEFAULT_MAG = find_mag()

# ── All 18 ruleset values from sky130A.tcl lines 51-72 ───────────────────────
RS = ("poly_surround 0.08 diff_surround 0.06 gate_to_diffcont 0.145 "
      "gate_to_polycont 0.275 gate_extension 0.13 diff_extension 0.29 "
      "contact_size 0.17 via_size 0.17 metal_surround 0.08 sub_surround 0.18 "
      "diff_spacing 0.28 poly_spacing 0.21 diff_poly_space 0.075 "
      "diff_gate_space 0.20 metal_spacing 0.23 mmetal_spacing 0.14 "
      "res_to_cont 0.20 res_diff_spacing 0.20")

# ── Device sweep config ───────────────────────────────────────────────────────

_base_sweep = [
    (0.42,0.15,1), (1.00,0.15,1), (1.00,0.15,2), (2.00,0.15,4),
    (2.00,0.35,2), (4.00,0.15,4), (0.42,0.50,1), (1.00,0.50,2),
    (0.42,1.00,1), (1.00,2.00,2), (2.00,5.00,2), (4.00,10.0,4),
    (10.0,0.15,1), (10.0,0.15,4), (10.0,0.15,8),
    (4.0,1.00,2),  (4.0,2.00,4),  (4.0,5.00,8),
    (10.0,10.0,1), (10.0,10.0,4), (10.0,10.0,10)
]
_ratio_sweep = []
for _l in [0.15, 0.5, 1.0, 2.0, 5.0]:
    for _ratio in [2, 3, 4, 8]:
        _w = max(0.42, round(_ratio * _l, 2))
        _ratio_sweep.extend([(_w, _l, 1), (_w, _l, 2), (_w, _l, 4)])

MOSFET_SWEEP = sorted(list(set(_base_sweep + _ratio_sweep)))
MOSFET_DEVS = [
    "nfet_01v8","pfet_01v8","nfet_01v8_lvt","pfet_01v8_hvt","pfet_01v8_lvt",
    "nfet_g5v0d10v5","pfet_g5v0d10v5","nfet_g5v0d16v0","pfet_g5v0d16v0",
]
POLY_RES = [   # (dev, fixed_w or None)
    ("res_high_po_0p35",0.35),("res_high_po_0p69",0.69),
    ("res_high_po_1p41",1.41),("res_high_po_2p85",2.85),
    ("res_high_po_5p73",5.73),("res_xhigh_po_0p35",0.35),
    ("res_xhigh_po_0p69",0.69),("res_xhigh_po_1p41",1.41),
    ("res_xhigh_po_2p85",2.85),("res_xhigh_po_5p73",5.73),
    ("res_generic_po",None),("res_generic_nd",None),("res_generic_pd",None),
]
RES_L     = [2.0,5.0,10.0]
RES_W_GEN = [0.5,1.0,2.0]

MIM_CAPS  = [("cap_mim_m3_1",5,5),("cap_mim_m3_1",10,10),("cap_mim_m3_1",20,20),
             ("cap_mim_m3_2",5,5),("cap_mim_m3_2",10,10)]
VAR_CAPS  = [("cap_var_lvt",0.42,0.15),("cap_var_lvt",1.0,0.15),("cap_var_lvt",2.0,0.15),
             ("cap_var_hvt",0.42,0.15),("cap_var_hvt",1.0,0.15),("cap_var_hvt",2.0,0.15)]

# BJTs: confirmed working with rf_ prefix
BJTS = {
    "npn_05v5_W1p00L1p00": "sky130_fd_pr__rf_npn_05v5_W1p00L1p00",
    "npn_05v5_W1p00L2p00": "sky130_fd_pr__rf_npn_05v5_W1p00L2p00",
    "pnp_05v5_W0p68L0p68": "sky130_fd_pr__rf_pnp_05v5_W0p68L0p68",
    "pnp_05v5_W3p40L3p40": "sky130_fd_pr__rf_pnp_05v5_W3p40L3p40",
}

# ── TCL builder ───────────────────────────────────────────────────────────────

def build_tcl(mag_dir: str) -> tuple[str, list[dict]]:
    tasks  = []
    blocks = []

    blocks.append("drc off\ncrashbackups stop\n")

    # ── THE FIX: patch every sky130 proc that uses $sky130::ruleset ──────────
    # string map replaces the variable reference with an inline dict literal.
    # Confirmed working by comprehensive_test.py (A3 approach + proc body analysis).
    blocks.append(f"""
set __RS_INLINE__ {{[dict create {RS}]}}
foreach __p__ [info procs sky130::*] {{
    catch {{
        set __body__ [info body $__p__]
        if {{[string match "*sky130::ruleset*" $__body__]}} {{
            set __new__ [string map [list {{$sky130::ruleset}} $__RS_INLINE__] $__body__]
            proc $__p__ [info args $__p__] $__new__
        }}
    }}
}}
puts "PATCH_DONE"
""")

    cn = 0  # unique cell counter

    def meas(tag, dev_ns, param_tcl):
        nonlocal cn; cn += 1
        cell = f"M{cn:04d}"
        return (f"\nset __p__ [{dev_ns}_defaults]\n"
                f"foreach {{__k__ __v__}} {param_tcl} {{ dict set __p__ $__k__ $__v__ }}\n"
                f"cellname create {cell}\nload {cell}\n"
                f"if {{[catch {{{dev_ns}_draw $__p__}} __e__]}} {{\n"
                f"    puts \"MEAS_ERR|{tag}|$__e__\"\n"
                f"}} else {{\n"
                f"    select top cell\n    expand\n"
                f"    puts \"MEAS_START|{tag}\"\n    box\n    puts \"MEAS_END|{tag}\"\n"
                f"}}\n")

    ns = "sky130::sky130_fd_pr__{}"

    # MOSFETs
    for dev in MOSFET_DEVS:
        for (w,l,nf) in MOSFET_SWEEP:
            tag = f"mosfet|{dev}|{w}|{l}|{nf}"
            blocks.append(meas(tag, ns.format(dev), f"{{w {w} l {l} nf {nf} m 1}}"))
            tasks.append({"tag":tag,"cat":"mosfet","dev":dev,"w":w,"l":l,"nf":nf,"m":1})

    # Resistors
    for (dev, fw) in POLY_RES:
        widths = [fw] if fw else RES_W_GEN
        for w in widths:
            for l in RES_L:
                tag = f"res|{dev}|{w}|{l}"
                blocks.append(meas(tag, ns.format(dev), f"{{w {w} l {l} m 1 nx 1}}"))
                tasks.append({"tag":tag,"cat":"res","dev":dev,"w":w,"l":l})

    # MIM caps
    for (dev,w,l) in MIM_CAPS:
        tag = f"mim|{dev}|{w}|{l}"
        blocks.append(meas(tag, ns.format(dev), f"{{w {w} l {l}}}"))
        tasks.append({"tag":tag,"cat":"mim","dev":dev,"w":w,"l":l})

    # Varactor caps
    for (dev,w,l) in VAR_CAPS:
        tag = f"var|{dev}|{w}|{l}"
        blocks.append(meas(tag, ns.format(dev), f"{{w {w} l {l} nf 1 m 1}}"))
        tasks.append({"tag":tag,"cat":"var","dev":dev,"w":w,"l":l})

    # BJTs — direct MAG load (no _draw needed)
    blocks.append(f"\naddpath {mag_dir}\n")
    for dev_key, mag_cell in BJTS.items():
        tag = f"bjt|{dev_key}"
        blocks.append(f"\nload {mag_cell}\n"
                      f"select top cell\nexpand\n"
                      f"puts \"MEAS_START|{tag}\"\nbox\nputs \"MEAS_END|{tag}\"\n")
        tasks.append({"tag":tag,"cat":"bjt","dev":dev_key})

    blocks.append("\nquit -noprompt\n")
    return "".join(blocks), tasks

# ── Parser ────────────────────────────────────────────────────────────────────

BOX_RE = re.compile(
    r'microns:\s+([\d.]+)\s+x\s+([\d.]+)\s+'
    r'\(\s*([\d.-]+),\s+([\d.-]+)\s*\),\s*\(\s*([\d.-]+),\s+([\d.-]+)'
)

def parse(out: str, tasks: list) -> dict:
    results = {}
    lines = out.splitlines(); i = 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("MEAS_ERR|"):
            parts = l.split("|",2)
            print(f"  ✘  {parts[1]}  →  {parts[2] if len(parts)>2 else ''}")
        elif l.startswith("MEAS_START|"):
            tag = l.split("|",1)[1]
            box_lines = []; i += 1
            while i < len(lines) and not lines[i].startswith("MEAS_END|"):
                box_lines.append(lines[i]); i += 1
            m = BOX_RE.search("\n".join(box_lines))
            if m:
                w,h = float(m.group(1)),float(m.group(2))
                if w > 0.01:
                    results[tag] = {"w_um":round(w,4),"h_um":round(h,4),"area_um2":round(w*h,4)}
                    print(f"  ✅  {tag:<55}  {w:.3f}µm × {h:.3f}µm")
                else:
                    print(f"  ✘  {tag}  →  empty cell {w}×{h}")
            else:
                print(f"  ✘  {tag}  →  no bbox")
        i += 1
    return results

def build_db(tasks, results):
    db: dict[str,Any] = {"pdk":"sky130A","units":"micrometers",
        "mosfets":{d:{"sweep":[]} for d in MOSFET_DEVS},
        "poly_resistors":{},"mim_caps":{},"var_caps":{},"bjts":{}}
    ok = 0
    for t in tasks:
        tag = t["tag"]; r = results.get(tag)
        if not r: continue
        ok += 1; cat = t["cat"]; dev = t["dev"]
        entry = {k:v for k,v in t.items() if k not in("tag","cat","dev")}
        entry.update(r)
        if   cat=="mosfet": db["mosfets"][dev]["sweep"].append(entry)
        elif cat=="res":    db["poly_resistors"].setdefault(dev,{"sweep":[]})["sweep"].append(entry)
        elif cat=="mim":    db["mim_caps"].setdefault(dev,{"sweep":[]})["sweep"].append(entry)
        elif cat=="var":    db["var_caps"].setdefault(dev,{"sweep":[]})["sweep"].append(entry)
        elif cat=="bjt":    db["bjts"][dev] = r
    print(f"\n  {ok}/{len(tasks)} successful")
    return db

def fit_models(db):
    try: import numpy as np
    except ImportError: print("[skip] numpy not found"); return db
    print("\n── Fitting models ──────────────────────────────────────────────")
    for dev,data in db["mosfets"].items():
        pts = data.get("sweep",[]); 
        if len(pts)<3: continue
        X_h = np.array([[p["w"], 1.] for p in pts])
        y_h = np.array([p["h_um"] for p in pts])
        X_w = np.array([[p["l"]*p["nf"], p["nf"], 1.] for p in pts])
        y_w = np.array([p["w_um"] for p in pts])
        try:
            c_h, *_ = np.linalg.lstsq(X_h, y_h, rcond=None)
            c_w, *_ = np.linalg.lstsq(X_w, y_w, rcond=None)
            ah, bh = [round(float(x), 4) for x in c_h]
            aw, bw, cw = [round(float(x), 4) for x in c_w]
            data["model"] = {
                "formula": "area=(ah*w+bh)*(aw*l*nf+bw*nf+cw)",
                "ah": ah, "bh": bh, "aw": aw, "bw": bw, "cw": cw
            }
            print(f"  {dev:<36}  ah={ah} bh={bh} aw={aw} bw={bw} cw={cw}")
        except Exception as e: print(f"  [warn] {dev}: {e}")
    for dev,data in db["poly_resistors"].items():
        pts = sorted(data.get("sweep",[]),key=lambda p:p["w"]); models=[]
        for wv,g in groupby(pts,key=lambda p:p["w"]):
            g=list(g)
            if len(g)<2: continue
            X=np.array([[p["l"],1.] for p in g]); y=np.array([p["area_um2"] for p in g])
            try:
                c,*_=np.linalg.lstsq(X,y,rcond=None); s,ic=[round(float(x),4) for x in c]
                models.append({"w":wv,"slope":s,"intercept":ic})
                print(f"  {dev:<36}  w={wv}: slope={s} ic={ic}")
            except: pass
        if models: data["model"]=models
    for dev,data in db["mim_caps"].items():
        pts=data.get("sweep",[]); 
        if len(pts)<2: continue
        try:
            import numpy as np
            b=round(float(np.mean([((p["area_um2"]**0.5)-(p["w"]+p["l"])/2)/2 for p in pts])),4)
            data["model"]={"formula":"area=(w+2b)*(l+2b)","border_um":b}
            print(f"  {dev:<36}  border={b}µm")
        except Exception as e: print(f"  [warn] {dev}: {e}")
    return db

# ── Runner ────────────────────────────────────────────────────────────────────

def spin(stop,start):
    f=["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]; i=0
    while not stop.is_set():
        sys.stdout.write(f"\r  {f[i%10]}  Magic running...  {time.time()-start:.0f}s  ")
        sys.stdout.flush(); time.sleep(0.12); i+=1
    sys.stdout.write("\r"+" "*50+"\r"); sys.stdout.flush()

def find_rc():
    for c in [os.environ.get("SKY130_MAGICRC",""),
              os.path.join(os.environ.get("PDK_ROOT",""),"sky130A/libs.tech/magic/sky130A.magicrc"),
              DEFAULT_RC]:
        if c and Path(c).exists(): return c
    return ""

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--magicrc",  default="")
    p.add_argument("--mag-path", default=DEFAULT_MAG)
    p.add_argument("--magic",    default="magic")
    p.add_argument("--out",      default="device_db.json")
    p.add_argument("--timeout",  type=int, default=300)
    p.add_argument("--dry-run",  action="store_true")
    p.add_argument("--no-fit",   action="store_true")
    args = p.parse_args()

    rc = args.magicrc or find_rc()
    if not rc and not args.dry_run:
        print("[ERROR] sky130A.magicrc not found"); sys.exit(1)

    tcl, tasks = build_tcl(args.mag_path)

    if args.dry_run:
        print(tcl[:2000]); print(f"\n[DRY-RUN] {len(tasks)} measurements"); return

    print(f"[INFO] magicrc  : {rc}")
    print(f"[INFO] devices  : {len(tasks)} in ONE Magic session")
    print(f"[INFO] output   : {args.out}\n")

    stop = threading.Event()
    threading.Thread(target=spin,args=(stop,time.time()),daemon=True).start()
    try:
        res = subprocess.run(["magic","-rcfile",rc,"-noconsole","-dnull"],
                             input=tcl,capture_output=True,text=True,timeout=args.timeout)
        out = res.stdout + res.stderr
    except subprocess.TimeoutExpired:
        stop.set(); print(f"❌ Timeout"); sys.exit(1)
    finally:
        stop.set(); time.sleep(0.2)

    print(f"  Magic exited rc={res.returncode}\n")
    if "PATCH_DONE" not in out:
        print("  ⚠ PATCH_DONE not seen — patching may have failed")

    results = parse(out, tasks)
    db = build_db(tasks, results)
    if not args.no_fit: db = fit_models(db)
    Path(args.out).write_text(json.dumps(db,indent=2))
    print(f"\n[DONE] → {Path(args.out).resolve()}")

if __name__=="__main__":
    main()
