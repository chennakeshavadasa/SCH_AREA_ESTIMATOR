#!/usr/bin/env python3
"""
sky130_area_estimator.py
────────────────────────
Reads a SPICE netlist (xschem output) and estimates pre-layout area
using device_db.json produced by sky130_measure_devices.py.

Usage:
    python3 sky130_area_estimator.py --netlist my_design.spice --db device_db.json
    python3 sky130_area_estimator.py --netlist my_design.spice   # auto-finds db

Also callable as a library:
    from sky130_area_estimator import estimate
    report = estimate("my_design.spice", "device_db.json")
"""

import argparse, json, re, sys
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# SPICE NETLIST PARSER
# Handles xschem-style netlists:
#   Xname n1 n2 ... sky130_fd_pr__nfet_01v8 W=1 L=0.15 nf=4 m=2
#   Xname n1 n2 ... sky130_fd_pr__res_high_po_0p35 l=10 m=1
# ─────────────────────────────────────────────────────────────────────────────

# Recognise sky130 device model names
_SKY130_MODEL = re.compile(r'sky130_fd_(?:pr|bs)__\S+', re.IGNORECASE)
# Parameter key=value
_PARAM_RE     = re.compile(r'(\w+)\s*=\s*([^\s]+)')


def parse_spice(path: str) -> list[dict]:
    """
    Returns list of instances:
      {"model": str, "params": {"w": float, "l": float, "nf": int, "m": int, ...}}
    """
    instances = []
    text = Path(path).read_text(errors="replace")

    # Join continuation lines (lines ending in \)
    text = re.sub(r'\\\s*\n\+?\s*', ' ', text)

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('*') or line.startswith('.'):
            continue
        if not line.upper().startswith('X'):
            continue

        # Find model name
        m = _SKY130_MODEL.search(line)
        if not m:
            continue
        model = m.group(0).lower()
        # Strip sky130_fd_pr__ prefix to match db keys
        dev = re.sub(r'^sky130_fd_(?:pr|bs)__', '', model)

        # Parse parameters
        params: dict[str, Any] = {"w": None, "l": None, "nf": 1, "m": 1}
        for k, v in _PARAM_RE.findall(line):
            key = k.lower()
            try:
                fval = float(v)
            except ValueError:
                continue
            if key in ("w", "width"):
                params["w"] = fval
            elif key in ("l", "length"):
                params["l"] = fval
            elif key in ("nf", "fingers", "nfin"):
                params["nf"] = max(1, int(round(fval)))
            elif key == "m":
                params["m"] = max(1, int(round(fval)))

        instances.append({"model": model, "dev": dev, "params": params,
                           "raw": line[:80]})

    return instances


# ─────────────────────────────────────────────────────────────────────────────
# AREA LOOKUP FROM DB
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_mosfet(db: dict, dev: str, w: float, l: float, nf: int) -> float | None:
    """Use fitted model if available, else nearest-neighbour from sweep."""
    data = db.get("mosfets", {}).get(dev)
    if not data:
        return None

    model = data.get("model")
    if model:
        if "ah" in model:
            h = max(0.0, model["ah"] * w + model["bh"])
            w_box = max(0.0, model["aw"] * (l * nf) + model["bw"] * nf + model["cw"])
            return h * w_box
        else:
            a, b, c = model["a"], model["b"], model["c"]
            return max(0.0, a * (w * nf) + b * l + c)

    # Nearest neighbour fallback
    pts = data.get("sweep", [])
    if not pts:
        return None
    best = min(pts, key=lambda p: abs(p["w"]-w) + abs(p["l"]-l) + abs(p["nf"]-nf))
    # Scale by w*nf/nf_ref (crude but better than nothing)
    scale = (w * nf) / (best["w"] * best["nf"]) if best["w"] * best["nf"] else 1
    return best["area_um2"] * scale


def _lookup_res(db: dict, dev: str, w: float | None, l: float) -> float | None:
    data = db.get("poly_resistors", {}).get(dev)
    if not data:
        return None
    models = data.get("model", [])
    if not models:
        # Nearest neighbour
        pts = data.get("sweep", [])
        if not pts:
            return None
        best = min(pts, key=lambda p: abs(p["l"]-l) + (abs(p["w"]-w) if w else 0))
        return best["area_um2"]

    # Find model for closest w
    if w is None:
        m = models[0]
    else:
        m = min(models, key=lambda x: abs(x["w"]-w))
    return max(0.0, m["slope"] * l + m["intercept"])


def _lookup_mim(db: dict, dev: str, w: float, l: float) -> float | None:
    data = db.get("mim_caps", {}).get(dev)
    if not data:
        return None
    model = data.get("model")
    if model:
        b = model["border_um"]
        return (w + 2*b) * (l + 2*b)
    # Nearest neighbour
    pts = data.get("sweep", [])
    if not pts:
        return None
    best = min(pts, key=lambda p: abs(p["w"]-w) + abs(p["l"]-l))
    return best["area_um2"]


def _lookup_var(db: dict, dev: str, w: float, l: float) -> float | None:
    data = db.get("var_caps", {}).get(dev)
    if not data:
        return None
    model = data.get("model")
    if model:
        return max(0.0, model["a"]*w + model["b"]*l + model["c"])
    pts = data.get("sweep", [])
    if not pts:
        return None
    best = min(pts, key=lambda p: abs(p["w"]-w) + abs(p["l"]-l))
    return best["area_um2"]


def _lookup_bjt(db: dict, dev: str) -> float | None:
    bjts = db.get("bjts", {})
    for k, v in bjts.items():
        if k.lower() == dev.lower():
            return v["area_um2"]
    return None


# Device category classifier
def _classify(dev: str) -> str:
    if any(x in dev for x in ["nfet","pfet"]) and "cap_var" not in dev:
        return "mosfet"
    if "res_" in dev:
        return "res"
    if "cap_mim" in dev:
        return "mim"
    if "cap_var" in dev:
        return "var"
    if any(x in dev for x in ["npn","pnp"]):
        return "bjt"
    return "unknown"


def lookup_area(db: dict, inst: dict) -> tuple[float | None, str]:
    """Returns (area_per_instance_um2, note_string)."""
    dev    = inst["dev"]
    params = inst["params"]
    w      = params.get("w")
    l      = params.get("l")
    nf     = params.get("nf", 1)
    m      = params.get("m", 1)
    cat    = _classify(dev)

    area_1 = None
    note   = ""

    if cat == "mosfet":
        if w is None or l is None:
            note = "missing W or L"
        else:
            area_1 = _lookup_mosfet(db, dev, w, l, nf)
            note = f"w={w}µm l={l}µm nf={nf}"

    elif cat == "res":
        if l is None:
            note = "missing L"
        else:
            area_1 = _lookup_res(db, dev, w, l)
            note = f"l={l}µm" + (f" w={w}µm" if w else "")

    elif cat == "mim":
        if w is None or l is None:
            note = "missing W or L"
        else:
            area_1 = _lookup_mim(db, dev, w, l)
            note = f"w={w}µm l={l}µm"

    elif cat == "var":
        if w is None or l is None:
            note = "missing W or L"
        else:
            area_1 = _lookup_var(db, dev, w, l)
            note = f"w={w}µm l={l}µm"

    elif cat == "bjt":
        area_1 = _lookup_bjt(db, dev)
        note = "fixed size"

    else:
        note = "unrecognised device"

    if area_1 is not None:
        return area_1 * m, note + (f" ×m={m}" if m > 1 else "")
    return None, note


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

def estimate(netlist: str, db_path: str,
             routing_overhead: float = 1.30) -> dict:
    """
    Main estimation function. Returns report dict.
    """
    db        = json.loads(Path(db_path).read_text())
    instances = parse_spice(netlist)

    rows       = []
    total_area = 0.0
    unknown    = []

    for inst in instances:
        area, note = lookup_area(db, inst)
        if area is not None:
            total_area += area
            rows.append({
                "dev":    inst["dev"],
                "model":  inst["model"],
                "params": inst["params"],
                "area":   round(area, 4),
                "note":   note,
            })
        else:
            unknown.append({"dev": inst["dev"], "reason": note})

    routing_area  = total_area * (routing_overhead - 1)
    total_with_ro = total_area + routing_area
    side          = total_with_ro ** 0.5

    return {
        "netlist":          netlist,
        "db":               db_path,
        "instances_parsed": len(instances),
        "instances_costed": len(rows),
        "instances_unknown":len(unknown),
        "device_area_um2":  round(total_area, 2),
        "routing_overhead": routing_overhead,
        "routing_area_um2": round(routing_area, 2),
        "total_area_um2":   round(total_with_ro, 2),
        "equiv_side_um":    round(side, 2),
        "breakdown":        rows,
        "unknown":          unknown,
    }


def print_report(r: dict):
    W = 72
    print("━" * W)
    print(f"  Sky130A Pre-Layout Area Estimator")
    print(f"  Netlist : {r['netlist']}")
    print(f"  DB      : {r['db']}")
    print("━" * W)
    print(f"  {'Device':<38} {'Params':<22} {'Area (µm²)':>10}")
    print(f"  {'─'*38} {'─'*22} {'─'*10}")

    # Group by device type
    from itertools import groupby as _gb
    rows = sorted(r["breakdown"], key=lambda x: x["dev"])
    for dev, grp in _gb(rows, key=lambda x: x["dev"]):
        grp = list(grp)
        dev_total = sum(x["area"] for x in grp)
        for x in grp:
            print(f"  {x['dev'][-38:]:<38} {x['note']:<22} {x['area']:>10.3f}")
        if len(grp) > 1:
            print(f"  {'  subtotal':>38}  {'':22} {dev_total:>10.3f}")

    print(f"  {'─'*38} {'─'*22} {'─'*10}")
    print(f"  {'Devices subtotal':38} {'':22} {r['device_area_um2']:>10.2f}")
    print(f"  {'Routing overhead (×'+str(r['routing_overhead'])+')':38} {'':22} {r['routing_area_um2']:>10.2f}")
    print(f"  {'─'*38} {'─'*22} {'─'*10}")
    print(f"  {'TOTAL ESTIMATED AREA':38} {'':22} {r['total_area_um2']:>10.2f} µm²")
    print(f"  {'Equivalent square side':38} {'':22} {r['equiv_side_um']:>10.2f} µm")

    if r["unknown"]:
        print(f"\n  ⚠  {r['instances_unknown']} uncosted device(s):")
        for u in r["unknown"]:
            print(f"     {u['dev']}  →  {u['reason']}")

    print("━" * W)
    print(f"  Parsed {r['instances_parsed']} instances, "
          f"costed {r['instances_costed']}, "
          f"skipped {r['instances_unknown']}")
    print("━" * W)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--netlist", required=True, help="Path to .spice netlist")
    p.add_argument("--db",      default="device_db.json",
                   help="device_db.json from sky130_measure_devices.py")
    p.add_argument("--routing", type=float, default=1.30,
                   help="Routing overhead multiplier (default 1.30)")
    p.add_argument("--budget",  type=float, default=None,
                   help="Total layout area budget available (in um^2)")
    p.add_argument("--json",    action="store_true",
                   help="Output raw JSON instead of table")
    p.add_argument("--out",     default="",
                   help="Save JSON report to file")
    args = p.parse_args()

    if not Path(args.netlist).exists():
        print(f"[ERROR] Netlist not found: {args.netlist}"); sys.exit(1)
    if not Path(args.db).exists():
        print(f"[ERROR] DB not found: {args.db}"); sys.exit(1)

    report = estimate(args.netlist, args.db, args.routing)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
        if args.budget is not None:
            left = args.budget - report["total_area_um2"]
            pct = (report["total_area_um2"] / args.budget) * 100
            print(f"  {'Area Budget Allowed':38} {'':22} {args.budget:>10.2f} µm²")
            print(f"  {'Area Left Over':38} {'':22} {left:>10.2f} µm²")
            print(f"  {'Utilization':38} {'':22} {pct:>10.1f} %")
            if left < 0:
                print(f"  WARNING: You are OVER BUDGET by {abs(left):.2f} µm²!")
            else:
                print(f"  INFO: You are within budget.")
            print("━" * 72)

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
        print(f"\n[SAVED] {args.out}")


if __name__ == "__main__":
    main()
