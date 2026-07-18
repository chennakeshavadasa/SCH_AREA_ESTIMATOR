#!/usr/bin/env python3
"""
ihp130_area_estimator.py
========================
Schematic-level layout area estimator for the IHP SG13G2 130nm BiCMOS PDK.

Parses an ngspice/xschem SPICE netlist, looks up each device instance in the
IHP130 device_db.json, applies the bounding-box area model, applies routing
overhead, and reports the estimated total layout area.

Supports:
  - LV/HV MOSFETs : sg13_lv_nmos, sg13_lv_pmos, sg13_hv_nmos, sg13_hv_pmos
  - Poly resistors : rsil, rppd, rhigh
  - MIM capacitor  : cap_cmim
  - NPN HBTs       : npn13g2, npn13g2l, npn13g2v

Usage:
  python3 ihp130_area_estimator.py --netlist /path/to/design.spice
  python3 ihp130_area_estimator.py --netlist /path/to/design.spice --budget "200um x 200um"
  python3 ihp130_area_estimator.py --netlist /path/to/design.spice --db /custom/device_db.json
  python3 ihp130_area_estimator.py --netlist /path/to/design.spice --verbose

IHP130 SPICE conventions (xschem output):
  - Dimensions are in SI units: W=2e-6 (= 2um) or W=2u (= 2um)
  - MOSFETs : XM1 d g s b sg13_lv_nmos W=2e-6 L=0.13e-6 ng=1 m=1
              (ng = number of gate fingers; some netlists use nf instead)
  - Resistors: XR1 n1 n2 rsil w=0.5e-6 l=1.5e-6 m=1 b=0
  - MIM caps : XC1 n1 n2 cap_cmim W=5e-6 L=5e-6 m=1
  - HBTs     : XQ1 c b e npn13g2 Nx=1 m=1         [npn13g2: fixed l=0.9um]
               XQ1 c b e npn13g2l le=2e-6 Nx=1 m=1 [npn13g2l/v: variable l]
               Q1  c b e npn13g2  Nx=1 m=1          [Q-prefix also supported]

NOTE: The database ships with PLACEHOLDER coefficients.
      Run scripts/ihp130_measure_devices.py against a real PDK installation
      to populate accurate coefficients before relying on these estimates.
"""

import argparse
import json
import math
import os
import re
import sys

# ─── Default paths ──────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB   = os.path.join(SCRIPT_DIR, "ihp130", "device_db.json")
ROUTING_MULT = 1.30   # 30 % routing/spacing overhead (same as SKY130/GF180 baseline)

# ─── IHP130 device name sets ─────────────────────────────────────────────────
MOSFET_NAMES  = {"sg13_lv_nmos", "sg13_lv_pmos", "sg13_hv_nmos", "sg13_hv_pmos"}
RESISTOR_NAMES = {"rsil", "rppd", "rhigh"}
CAP_NAMES      = {"cap_cmim", "cap_rfcmim"}
HBT_NAMES      = {"npn13g2", "npn13g2l", "npn13g2v"}
ALL_KNOWN      = MOSFET_NAMES | RESISTOR_NAMES | CAP_NAMES | HBT_NAMES


# ─── Unit helpers ────────────────────────────────────────────────────────────

_SUFFIX_MAP = {
    "f": 1e-15, "p": 1e-12, "n": 1e-9,
    "u": 1e-6,  "m": 1e-3,  "k": 1e3,
    "meg": 1e6, "g": 1e9,   "t": 1e12,
}

def spice_to_um(value_str: str) -> float:
    """
    Convert a SPICE dimension string to micrometers.

    Handles:
      '2e-6'  → 2.0 um
      '2u'    → 2.0 um
      '130n'  → 0.13 um
      '0.13u' → 0.13 um
      '2'     → interpreted as 2 um when ≥ 0.01, otherwise as meters → *1e6

    If neither suffix nor scientific notation is present and the value is very
    small (< 0.01), it is assumed to already be in meters and converted to um.
    """
    s = value_str.strip().lower().rstrip(")")   # strip trailing ) sometimes present

    # Scientific notation (e.g. 2e-6, 1.3e-7): treat as meters → um
    if re.search(r"[eE][-+]?\d", s):
        try:
            v_m = float(s)
            return v_m * 1e6
        except ValueError:
            pass

    # SPICE suffixes (longest first to avoid 'm' consuming 'meg')
    for sfx in ("meg", "f", "p", "n", "u", "m", "k", "g", "t"):
        if s.endswith(sfx):
            try:
                num = float(s[: -len(sfx)])
                v_si = num * _SUFFIX_MAP[sfx]
                return v_si * 1e6   # SI → um
            except ValueError:
                pass

    # Plain float
    try:
        v = float(s)
        # Heuristic: bare floats < 0.01 are almost certainly in meters
        if abs(v) < 0.01:
            return v * 1e6
        return v   # already in um (legacy/explicit)
    except ValueError:
        raise ValueError(f"Cannot parse SPICE dimension: {value_str!r}")


def parse_kv_params(tokens: list) -> dict:
    """
    Extract KEY=VALUE pairs from a list of SPICE tokens.
    Returns a dict with lowercase keys and raw string values.
    """
    params = {}
    for tok in tokens:
        if "=" in tok:
            k, _, v = tok.partition("=")
            params[k.strip().lower()] = v.strip()
    return params


# ─── Device name detector ────────────────────────────────────────────────────

def detect_device_name(tokens: list) -> str | None:
    """Return the first token that matches a known IHP130 device name."""
    for tok in tokens:
        clean = tok.split("=")[0].lower()   # skip 'key=value' forms
        if clean in ALL_KNOWN:
            return clean
    return None


# ─── Area model evaluators ───────────────────────────────────────────────────

def mosfet_area(model: dict, W_um: float, L_um: float, nf: int, m: int) -> float:
    """
    Decoupled bounding-box MOSFET area model (same formula as SKY130 / GF180):
      area = (ah*W + bh) * (aw*L*nf + bw*nf + cw) * m
    W = width per finger (um), L = gate length (um), nf = number of fingers.
    """
    ah, bh = model["ah"], model["bh"]
    aw, bw, cw = model["aw"], model["bw"], model["cw"]
    height = ah * W_um + bh
    width  = aw * L_um * nf + bw * nf + cw
    return height * width * m


def resistor_area(model_list: list, W_um: float, L_um: float, m: int) -> float:
    """
    Per-width linear area model for drawn poly resistors:
      area = slope * L + intercept   (for the closest W entry)
    Multiplied by m for parallel copies.
    """
    if not model_list:
        return 0.0
    # Find the model entry whose 'w' is closest to the drawn width
    best = min(model_list, key=lambda e: abs(e["w"] - W_um))
    area_single = best["slope"] * L_um + best["intercept"]
    return area_single * m


def cap_area(model: dict, W_um: float, L_um: float, m: int) -> float:
    """
    MIM capacitor area model (same as SKY130):
      area = (W + 2*border) * (L + 2*border) * m
    """
    b = model["border_um"]
    return (W_um + 2 * b) * (L_um + 2 * b) * m


def hbt_area_npn13g2(model: dict, nx: int, m_mul: int) -> float:
    """
    Fixed-emitter HBT (npn13g2):
      area = fixed_h * (aw * nx + bw) * m_mul
    nx = number of emitter stripes; m_mul = SPICE multiplier.
    """
    fixed_h = model["fixed_h"]
    aw, bw  = model["aw"], model["bw"]
    return fixed_h * (aw * nx + bw) * m_mul


def hbt_area_variable(model: dict, l_um: float, nx: int, m_mul: int) -> float:
    """
    Variable-emitter HBT (npn13g2l, npn13g2v):
      area = (ah * l + bh) * (aw * nx + bw) * m_mul
    l_um = emitter length (um); nx = emitter stripes.
    """
    ah, bh = model["ah"], model["bh"]
    aw, bw = model["aw"], model["bw"]
    height = ah * l_um + bh
    width  = aw * nx + bw
    return height * width * m_mul


# ─── SPICE netlist parser ────────────────────────────────────────────────────

class IHP130Instance:
    """Represents one parsed device instance from the SPICE netlist."""
    __slots__ = ("line_no", "raw", "device", "category",
                 "W", "L", "nf", "m", "nx", "le")

    def __init__(self, line_no, raw, device, category,
                 W=0.0, L=0.0, nf=1, m=1, nx=1, le=0.0):
        self.line_no  = line_no
        self.raw      = raw
        self.device   = device
        self.category = category   # 'mosfet', 'resistor', 'cap', 'hbt'
        self.W  = W     # gate/body width per finger (um) — or resistor/cap width
        self.L  = L     # gate length / body length (um)
        self.nf = nf    # number of fingers (ng alias handled)
        self.m  = m     # SPICE multiplier (array of identical devices)
        self.nx = nx    # emitter stripe count for HBTs (from Nx or m depending on device)
        self.le = le    # emitter length for npn13g2l/v


def _safe_um(params: dict, *keys) -> float | None:
    for k in keys:
        if k in params:
            try:
                return spice_to_um(params[k])
            except ValueError:
                pass
    return None


def _safe_int(params: dict, *keys, default: int = 1) -> int:
    for k in keys:
        if k in params:
            try:
                return max(1, int(float(params[k])))
            except ValueError:
                pass
    return default


def parse_netlist(path: str) -> list[IHP130Instance]:
    """
    Parse an IHP130 / xschem SPICE netlist.
    Returns a list of IHP130Instance objects for every recognized device.

    Handles:
      - Line continuations (+)
      - Case-insensitive keywords
      - Both 'ng' and 'nf' for gate fingers
      - Both Q-prefix (primitive BJT) and X-prefix (subcircuit) for HBTs
      - Dimensions in SI units (2e-6) or SPICE suffixes (2u, 130n)
    """
    instances = []
    raw_lines = []

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.rstrip("\n")
            if raw.lstrip().startswith("+"):
                # Continuation of previous line
                if raw_lines:
                    raw_lines[-1] = raw_lines[-1] + " " + raw.lstrip()[1:]
            else:
                raw_lines.append(raw)

    for line_no, line in enumerate(raw_lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("*") or stripped.startswith("."):
            continue

        tokens = stripped.split()
        if len(tokens) < 2:
            continue

        first = tokens[0].upper()
        kv    = parse_kv_params(tokens)

        # ── X-prefix: subcircuit instance (MOSFETs, resistors, caps, HBTs) ──
        if first.startswith("X"):
            dev = detect_device_name(tokens[1:])
            if dev is None:
                continue

            if dev in MOSFET_NAMES:
                W_um  = _safe_um(kv, "w") or 0.6
                L_um  = _safe_um(kv, "l") or 0.13
                nf    = _safe_int(kv, "ng", "nf", default=1)
                m_val = _safe_int(kv, "m", default=1)
                instances.append(IHP130Instance(
                    line_no, stripped, dev, "mosfet",
                    W=W_um, L=L_um, nf=nf, m=m_val))

            elif dev in RESISTOR_NAMES:
                W_um  = _safe_um(kv, "w") or 0.5
                L_um  = _safe_um(kv, "l") or 2.0
                m_val = _safe_int(kv, "m", "nx", default=1)
                instances.append(IHP130Instance(
                    line_no, stripped, dev, "resistor",
                    W=W_um, L=L_um, m=m_val))

            elif dev in CAP_NAMES:
                W_um  = _safe_um(kv, "w") or 2.0
                L_um  = _safe_um(kv, "l") or 2.0
                m_val = _safe_int(kv, "m", "mf", "nx", default=1)
                instances.append(IHP130Instance(
                    line_no, stripped, dev, "cap",
                    W=W_um, L=L_um, m=m_val))

            elif dev in HBT_NAMES:
                # 'm' in SPICE for HBTs maps to nx (emitter stripe count)
                nx_val = _safe_int(kv, "nx", "m", default=1)
                m_mul  = _safe_int(kv, "m", default=1)
                le_um  = _safe_um(kv, "le", "l") or 0.9
                instances.append(IHP130Instance(
                    line_no, stripped, dev, "hbt",
                    nx=nx_val, m=m_mul, le=le_um))

        # ── Q-prefix: primitive SPICE BJT (also used for npn13g2 in some tools) ──
        elif first.startswith("Q"):
            dev = detect_device_name(tokens)
            if dev is not None and dev in HBT_NAMES:
                nx_val = _safe_int(kv, "nx", "m", default=1)
                m_mul  = _safe_int(kv, "m", default=1)
                le_um  = _safe_um(kv, "le", "l") or 0.9
                instances.append(IHP130Instance(
                    line_no, stripped, dev, "hbt",
                    nx=nx_val, m=m_mul, le=le_um))

    return instances


# ─── Budget parser ────────────────────────────────────────────────────────────

def parse_budget(budget_str: str) -> tuple[float, float] | None:
    """
    Parse a budget specification into (X_um, Y_um).
    Accepts:
      "16000"             → sqrt(16000) × sqrt(16000)
      "100x150"           → 100 × 150
      "100um x 150um"     → 100 × 150
      "100 vs 150"        → 100 × 150
      "130um x 130um"     → 130 × 130
    Returns None on failure.
    """
    s = budget_str.strip().lower()
    # Remove 'um' suffixes and any whitespace around separators
    s = re.sub(r"um", "", s)
    # Try separator variants: x, vs, *
    m = re.search(r"([0-9.]+)\s*(?:x|\*|vs)\s*([0-9.]+)", s)
    if m:
        return float(m.group(1)), float(m.group(2))
    # Try plain number → square budget
    try:
        area = float(s)
        side = math.sqrt(area)
        return side, side
    except ValueError:
        return None


# ─── Main estimator ──────────────────────────────────────────────────────────

def estimate(args: argparse.Namespace) -> None:
    # Load device database
    db_path = args.db or DEFAULT_DB
    if not os.path.isfile(db_path):
        sys.exit(f"[ERROR] device_db.json not found at: {db_path}\n"
                 f"        Run scripts/ihp130_measure_devices.py to generate it.")

    with open(db_path) as fh:
        db = json.load(fh)

    if db.get("_warning"):
        print(f"[WARN] {db['_warning']}\n")

    # Parse netlist
    netlist_path = args.netlist
    if not os.path.isfile(netlist_path):
        sys.exit(f"[ERROR] Netlist not found: {netlist_path}")

    instances = parse_netlist(netlist_path)

    total_device_area_um2 = 0.0
    costed = 0
    skipped = 0
    skipped_list = []
    per_device_rows = []   # (device_name, area_um2, multiplier_note)

    for inst in instances:
        dev  = inst.device
        area = 0.0

        try:
            if inst.category == "mosfet":
                model = db["mosfets"][dev]["model"]
                area  = mosfet_area(model, inst.W, inst.L, inst.nf, inst.m)

            elif inst.category == "resistor":
                model_list = db["resistors"][dev]["model"]
                area       = resistor_area(model_list, inst.W, inst.L, inst.m)

            elif inst.category == "cap":
                cap_key = "cap_cmim"   # cap_rfcmim uses same model for area
                model   = db["mim_caps"][cap_key]["model"]
                area    = cap_area(model, inst.W, inst.L, inst.m)

            elif inst.category == "hbt":
                hbt_model = db["hbts"][dev]["model"]
                formula   = hbt_model.get("formula", "")
                if dev == "npn13g2":
                    area = hbt_area_npn13g2(hbt_model, inst.nx, inst.m)
                else:
                    area = hbt_area_variable(hbt_model, inst.le, inst.nx, inst.m)

            if area <= 0:
                raise ValueError("Zero/negative area computed")

            total_device_area_um2 += area
            per_device_rows.append((dev, area, inst))
            costed += 1

        except (KeyError, TypeError, ValueError) as exc:
            skipped += 1
            skipped_list.append((inst.line_no, dev, str(exc)))
            if args.verbose:
                print(f"  [SKIP] line {inst.line_no}: {dev} — {exc}")

    # ── Verbose per-device table ─────────────────────────────────────────────
    if args.verbose and per_device_rows:
        print(f"\n{'─'*72}")
        print(f"  {'Device':<22} {'W(um)':>7} {'L(um)':>7} {'nf/nx':>5}"
              f"  {'m':>3}  {'Area (µm²)':>12}")
        print(f"{'─'*72}")
        for dev, area, inst in per_device_rows:
            if inst.category == "mosfet":
                dims = f"{inst.W:7.3f}  {inst.L:7.3f}  {inst.nf:5d}  {inst.m:3d}"
            elif inst.category in ("resistor", "cap"):
                dims = f"{inst.W:7.3f}  {inst.L:7.3f}  {'—':>5}  {inst.m:3d}"
            else:   # hbt
                dims = f"{'—':>7}  {inst.le:7.3f}  {inst.nx:5d}  {inst.m:3d}"
            print(f"  {dev:<22} {dims}  {area:12.2f}")

    routing_overhead   = total_device_area_um2 * (ROUTING_MULT - 1.0)
    total_area_um2     = total_device_area_um2 + routing_overhead
    equiv_side_um      = math.sqrt(total_area_um2)

    # ── Output block ─────────────────────────────────────────────────────────
    W = 72
    sep_thin = f"  {'─'*W}"
    sep_bold = f"{'━'*(W+2)}"

    print(f"\n{sep_thin}")
    print(f"  {'Devices subtotal':<52} {total_device_area_um2:>12.2f} µm²")
    print(f"  {f'Routing overhead (×{ROUTING_MULT})':<52} {routing_overhead:>12.2f} µm²")
    print(f"{sep_thin}")
    print(f"  {'TOTAL ESTIMATED AREA':<52} {total_area_um2:>12.2f} µm²")
    print(f"  {'Equivalent square side':<52} {equiv_side_um:>12.2f} µm")
    print(f"{sep_bold}")
    print(f"  Parsed {len(instances)} instances, "
          f"costed {costed}, skipped {skipped}")
    print(f"{sep_bold}")

    if skipped_list and not args.verbose:
        print(f"\n  [INFO] {skipped} instance(s) skipped "
              f"(use --verbose to see details)")

    # ── Budget comparison ─────────────────────────────────────────────────────
    if args.budget:
        dims = parse_budget(args.budget)
        if dims is None:
            print(f"\n  [WARN] Could not parse budget: {args.budget!r}")
        else:
            bx, by         = dims
            budget_area    = bx * by
            leftover       = budget_area - total_area_um2
            utilization    = (total_area_um2 / budget_area) * 100.0

            print()
            print(f"  {'Area Budget Allowed':<52} {budget_area:>12.2f} µm²")
            print(f"  {'Area Left Over':<52} {leftover:>12.2f} µm²")
            print(f"  {'Utilization':<52} {utilization:>12.1f} %")

            if leftover < 0:
                print(f"  WARNING: You are OVER BUDGET by {abs(leftover):.2f} µm²!")
            elif utilization > 90:
                print(f"  CAUTION: Utilization is high ({utilization:.1f}%). "
                      f"Consider a larger floorplan.")
            else:
                print(f"  You have {leftover:.2f} µm² of headroom "
                      f"({100 - utilization:.1f}% unused).")

    print()


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Estimate layout area from an IHP130 SG13G2 SPICE netlist.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)

    parser.add_argument(
        "--netlist", "-n", required=True,
        help="Path to the target SPICE netlist (.spice/.spi/.cdl).")
    parser.add_argument(
        "--db", "-d", default=None,
        help=f"Path to device_db.json. Default: {DEFAULT_DB}")
    parser.add_argument(
        "--budget", "-b", default=None,
        help='Area budget. Examples: "16000", "130um x 130um", "100x150".')
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print per-instance area table and skip reasons.")

    args = parser.parse_args()
    estimate(args)


if __name__ == "__main__":
    main()
