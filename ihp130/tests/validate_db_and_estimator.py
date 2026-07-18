#!/usr/bin/env python3
"""
validate_db_and_estimator.py
============================
Rigorous, adversarial validation of the IHP130 area estimator after a
measurement sweep.  Tests every claim made by the measurement agent:

  1. DB structure — all 11 devices present, _warning removed, no _note "PLACEHOLDER"
  2. Coefficient sanity — values not equal to original placeholders
  3. Physics bounds — areas must be in physically plausible ranges for IHP130
  4. Model monotonicity — larger W / L / nf / nx → larger area
  5. Multiplier linearity — m=2 gives exactly 2× m=1
  6. Cross-device ordering — HV devices larger than LV; HBTs scale with nx
  7. Estimator integration — parse a sample SPICE netlist, check 0 skipped
  8. Known-netlist regression — total area within 50% of expected physics range

Run from the IHP130 directory:
    python3 tests/validate_db_and_estimator.py [--db device_db.json]

Exit code: 0 = all tests passed,  1 = one or more FAILED.
"""

import json
import math
import sys
import os
import argparse
import tempfile
import textwrap
from pathlib import Path

# ─── Known PLACEHOLDER coefficient values ────────────────────────────────────
# Any DB that still contains these has NOT been regenerated.
PLACEHOLDER_MOSFET = {
    "sg13_lv_nmos": dict(ah=1.0, bh=1.95, aw=1.0, bw=0.30, cw=1.55),
    "sg13_lv_pmos": dict(ah=1.0, bh=2.15, aw=1.0, bw=0.30, cw=1.55),
    "sg13_hv_nmos": dict(ah=1.0, bh=2.60, aw=1.0, bw=0.36, cw=2.05),
    "sg13_hv_pmos": dict(ah=1.0, bh=2.90, aw=1.0, bw=0.36, cw=2.20),
}
PLACEHOLDER_RES_SLOPE   = 2.10   # slope for W=0.5 in all resistors
PLACEHOLDER_CAP_BORDER  = 0.80
PLACEHOLDER_NPN_FIXED_H = 5.80

# ─── IHP130 physical bounds (min, max) µm² for specific device sizes ─────────
# Based on SG13G2 design rules + typical AMS layout knowledge.
# These are CONSERVATIVE bounds — real layouts should fall well within them.
PHYSICS_BOUNDS = {
    # (device, W_um, L_um, nf, m) → (area_min_um2, area_max_um2)
    # Single minimum-size LV NMOS
    ("sg13_lv_nmos", 0.15, 0.13, 1, 1): (0.5,  12.0),
    # Medium LV NMOS
    ("sg13_lv_nmos", 2.0,  0.13, 1, 1): (1.5,  40.0),
    # Wide multi-finger LV NMOS
    ("sg13_lv_nmos", 4.0,  0.13, 4, 1): (10.0, 120.0),
    # Minimum HV NMOS
    ("sg13_hv_nmos", 0.15, 0.45, 1, 1): (1.0,  20.0),
    # Medium HV NMOS — must be LARGER than LV equivalent
    ("sg13_hv_nmos", 2.0,  0.45, 1, 1): (1.5,  60.0),
    # LV PMOS — slightly larger than LV NMOS (n-well)
    ("sg13_lv_pmos", 2.0,  0.13, 1, 1): (1.5,  45.0),
    # HV PMOS
    ("sg13_hv_pmos", 2.0,  0.40, 1, 1): (1.5,  70.0),
    # npn13g2 single emitter
    ("npn13g2", None, 0.9, 1, 1): (5.0,  80.0),
    # npn13g2 max fingers
    ("npn13g2", None, 0.9, 10, 1): (30.0, 600.0),
    # npn13g2l variable emitter
    ("npn13g2l", None, 1.0, 1, 1): (5.0,  80.0),
    ("npn13g2l", None, 2.5, 4, 1): (30.0, 800.0),
    # npn13g2v high-power
    ("npn13g2v", None, 1.0, 1, 1): (8.0,  120.0),
}

# Resistor bounds: (device, W_um, L_um, m) → (area_min, area_max)
RES_BOUNDS = {
    ("rsil",  0.5, 1.0,  1): (1.0,  30.0),
    ("rsil",  0.5, 20.0, 1): (10.0, 150.0),
    ("rppd",  0.5, 1.0,  1): (1.0,  30.0),
    ("rhigh", 0.5, 1.0,  1): (1.0,  30.0),
}

# Cap bounds: (device, W_um, L_um, m) → (area_min, area_max)
CAP_BOUNDS = {
    ("cap_cmim", 5.0,  5.0,  1): (20.0, 60.0),
    ("cap_cmim", 10.0, 10.0, 1): (80.0, 200.0),
    ("cap_cmim", 2.0,  2.0,  1): (4.0,  25.0),
}

# ─── Test runner ─────────────────────────────────────────────────────────────

PASS  = "✓  PASS"
FAIL  = "✗  FAIL"
WARN  = "⚠  WARN"

results = []

def check(name, condition, detail=""):
    tag = PASS if condition else FAIL
    results.append((tag, name, detail))
    print(f"  {tag}  {name}")
    if detail:
        prefix = "         "
        for line in detail.splitlines():
            print(f"{prefix}{line}")
    return condition


def section(title):
    print(f"\n{'═'*70}")
    print(f"  {title}")
    print(f"{'─'*70}")


# ─── Model helpers ───────────────────────────────────────────────────────────

def mosfet_area(model, W, L, nf, m):
    ah, bh = model["ah"], model["bh"]
    aw, bw, cw = model["aw"], model["bw"], model["cw"]
    return (ah * W + bh) * (aw * L * nf + bw * nf + cw) * m

def resistor_area(entries, W, L, m):
    """Find nearest W entry and compute area."""
    closest = min(entries, key=lambda e: abs(e["w"] - W))
    return (closest["slope"] * L + closest["intercept"]) * m

def cap_area(model, W, L, m):
    b = model["border_um"]
    return (W + 2*b) * (L + 2*b) * m

def hbt_fixed_area(model, nx, m):
    return model["fixed_h"] * (model["aw"] * nx + model["bw"]) * m

def hbt_var_area(model, l, nx, m):
    return (model["ah"] * l + model["bh"]) * (model["aw"] * nx + model["bw"]) * m


# ─── Main validation ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Validate IHP130 device_db.json and area estimator")
    parser.add_argument("--db", default="device_db.json", help="Path to device_db.json")
    parser.add_argument("--estimator", default="ihp130_area_estimator.py")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        sys.exit(f"ERROR: DB not found: {db_path}")

    with open(db_path) as f:
        db = json.load(f)

    estimator = Path(args.estimator)
    if not estimator.exists():
        estimator = Path("../") / args.estimator
    if not estimator.exists():
        estimator = None

    print(f"\nIHP130 Area Estimator — Rigorous Validation")
    print(f"DB: {db_path.resolve()}")
    print(f"Estimator: {estimator or '(not found — skipping integration tests)'}")

    # ═══════════════════════════════════════════════════════════════════════
    section("1. DB STRUCTURE")

    # 1a. _warning must be GONE (agent claimed sweep completed)
    check("_warning key removed",
          "_warning" not in db,
          "_warning still present — sweep may not have completed all devices")

    # 1b. All 11 devices present
    expected_mosfets  = {"sg13_lv_nmos", "sg13_lv_pmos", "sg13_hv_nmos", "sg13_hv_pmos"}
    expected_resistors = {"rsil", "rppd", "rhigh"}
    expected_caps     = {"cap_cmim"}
    expected_hbts     = {"npn13g2", "npn13g2l", "npn13g2v"}

    for cat, expected in [
        ("mosfets",   expected_mosfets),
        ("resistors", expected_resistors),
        ("mim_caps",  expected_caps),
        ("hbts",      expected_hbts),
    ]:
        got = set(db.get(cat, {}).keys())
        missing = expected - got
        check(f"{cat}: all devices present ({len(expected)})",
              not missing,
              f"Missing: {missing}" if missing else "")

    # 1c. No _note containing "PLACEHOLDER" in any model
    placeholder_notes = []
    for cat in ("mosfets", "resistors", "mim_caps", "hbts"):
        for dev, entry in db.get(cat, {}).items():
            model = entry.get("model", {})
            if isinstance(model, list):
                for m in model:
                    if "PLACEHOLDER" in str(m.get("_note", "")):
                        placeholder_notes.append(f"{dev} (W={m.get('w')})")
            else:
                if "PLACEHOLDER" in str(model.get("_note", "")):
                    placeholder_notes.append(dev)
    check("No PLACEHOLDER _note in any model",
          not placeholder_notes,
          f"Still marked PLACEHOLDER: {placeholder_notes}" if placeholder_notes else "")

    # 1d. sweep[] arrays must be non-empty
    empty_sweeps = []
    for cat in ("mosfets", "resistors", "mim_caps", "hbts"):
        for dev, entry in db.get(cat, {}).items():
            if not entry.get("sweep"):
                empty_sweeps.append(f"{cat}/{dev}")
    check("All sweep arrays non-empty",
          not empty_sweeps,
          f"Empty sweeps: {empty_sweeps}" if empty_sweeps else "")


    # ═══════════════════════════════════════════════════════════════════════
    section("2. COEFFICIENTS DIFFER FROM ORIGINALS (placeholder check)")

    for dev, ph in PLACEHOLDER_MOSFET.items():
        m = db.get("mosfets", {}).get(dev, {}).get("model", {})
        still_placeholder = all(abs(m.get(k, -999) - v) < 1e-6 for k, v in ph.items())
        check(f"{dev}: coefficients changed from placeholder",
              not still_placeholder,
              "Coefficients are IDENTICAL to originals — sweep may not have run!" if still_placeholder else "")

    # Resistors
    for dev in ("rsil", "rppd", "rhigh"):
        model = db.get("resistors", {}).get(dev, {}).get("model", [])
        if model:
            still = abs(model[0].get("slope", -999) - PLACEHOLDER_RES_SLOPE) < 1e-6
            check(f"{dev}: slope changed from placeholder",
                  not still,
                  "slope unchanged from original 2.10!" if still else "")

    # Cap
    cap_model = db.get("mim_caps", {}).get("cap_cmim", {}).get("model", {})
    still_cap = abs(cap_model.get("border_um", -999) - PLACEHOLDER_CAP_BORDER) < 1e-6
    check("cap_cmim: border_um changed from placeholder",
          not still_cap,
          "border_um unchanged from original 0.80!" if still_cap else "")

    # HBT
    npn_model = db.get("hbts", {}).get("npn13g2", {}).get("model", {})
    still_npn = abs(npn_model.get("fixed_h", -999) - PLACEHOLDER_NPN_FIXED_H) < 1e-6
    check("npn13g2: fixed_h changed from placeholder",
          not still_npn,
          "fixed_h unchanged from original 5.80!" if still_npn else "")


    # ═══════════════════════════════════════════════════════════════════════
    section("3. PHYSICS BOUNDS (device areas must be physically plausible)")

    for (dev, W, L, nf_or_nx, m), (lo, hi) in PHYSICS_BOUNDS.items():
        cat = ("mosfets" if dev.startswith("sg13") else
               "hbts" if dev.startswith("npn") else None)
        if cat == "mosfets":
            model = db.get("mosfets", {}).get(dev, {}).get("model", {})
            if not model:
                check(f"{dev}(W={W},L={L},nf={nf_or_nx},m={m}): area in [{lo},{hi}]",
                      False, "device missing from DB"); continue
            area = mosfet_area(model, W, L, nf_or_nx, m)
        elif cat == "hbts":
            model = db.get("hbts", {}).get(dev, {}).get("model", {})
            if not model:
                check(f"{dev}(nx={nf_or_nx},m={m}): area in [{lo},{hi}]",
                      False, "device missing from DB"); continue
            nx = nf_or_nx
            if dev == "npn13g2":
                area = hbt_fixed_area(model, nx, m)
            else:
                area = hbt_var_area(model, L, nx, m)
        else:
            continue

        label = (f"{dev}(W={W},L={L},nf/nx={nf_or_nx},m={m}): "
                 f"area={area:.2f} in [{lo},{hi}]µm²")
        check(label, lo <= area <= hi,
              f"area={area:.2f} is OUTSIDE [{lo},{hi}]" if not (lo <= area <= hi) else "")

    # Resistors
    for (dev, W, L, m), (lo, hi) in RES_BOUNDS.items():
        model = db.get("resistors", {}).get(dev, {}).get("model", [])
        if not model:
            check(f"{dev}(W={W},L={L}): area in [{lo},{hi}]", False, "missing"); continue
        area = resistor_area(model, W, L, m)
        label = f"{dev}(W={W},L={L},m={m}): area={area:.2f} in [{lo},{hi}]µm²"
        check(label, lo <= area <= hi,
              f"area={area:.2f} OUTSIDE [{lo},{hi}]" if not (lo <= area <= hi) else "")

    # Caps
    for (dev, W, L, m), (lo, hi) in CAP_BOUNDS.items():
        model = db.get("mim_caps", {}).get(dev, {}).get("model", {})
        if not model:
            check(f"{dev}(W={W},L={L}): area in [{lo},{hi}]", False, "missing"); continue
        area = cap_area(model, W, L, m)
        # MIM cap: area MUST be larger than W*L (border adds overhead)
        plate = W * L
        label = f"{dev}(W={W},L={L}): area={area:.2f} in [{lo},{hi}]µm² (plate={plate})"
        check(label, lo <= area <= hi,
              f"area={area:.2f} OUTSIDE [{lo},{hi}]" if not (lo <= area <= hi) else "")
        check(f"{dev}(W={W},L={L}): area > plate area ({plate}µm²)",
              area > plate,
              f"area={area:.2f} ≤ plate={plate} — border must be positive")


    # ═══════════════════════════════════════════════════════════════════════
    section("4. MODEL MONOTONICITY")

    def get_mos(dev): return db.get("mosfets",{}).get(dev,{}).get("model",{})

    # Increasing W → larger area
    for dev in ("sg13_lv_nmos", "sg13_lv_pmos", "sg13_hv_nmos", "sg13_hv_pmos"):
        m = get_mos(dev)
        if not m: continue
        Lmin = 0.13 if "lv" in dev else 0.45 if "nmos" in dev else 0.40
        a1 = mosfet_area(m, 1.0, Lmin, 1, 1)
        a2 = mosfet_area(m, 4.0, Lmin, 1, 1)
        check(f"{dev}: area(W=4) > area(W=1)",
              a2 > a1, f"{a2:.2f} vs {a1:.2f}")

    # Increasing L → larger area
    for dev in ("sg13_lv_nmos", "sg13_hv_nmos"):
        m = get_mos(dev)
        if not m: continue
        Lmin = 0.13 if "lv" in dev else 0.45
        Lbig = 1.0
        a1 = mosfet_area(m, 1.0, Lmin, 1, 1)
        a2 = mosfet_area(m, 1.0, Lbig, 1, 1)
        check(f"{dev}: area(L={Lbig}) > area(L={Lmin})",
              a2 > a1, f"{a2:.2f} vs {a1:.2f}")

    # Increasing nf → larger area
    for dev in ("sg13_lv_nmos",):
        m = get_mos(dev)
        if not m: continue
        a1 = mosfet_area(m, 1.0, 0.13, 1, 1)
        a4 = mosfet_area(m, 1.0, 0.13, 4, 1)
        check(f"{dev}: area(nf=4) > area(nf=1)",
              a4 > a1, f"{a4:.2f} vs {a1:.2f}")

    # HBT: increasing nx → larger area
    for dev in ("npn13g2", "npn13g2l", "npn13g2v"):
        m = db.get("hbts",{}).get(dev,{}).get("model",{})
        if not m: continue
        if dev == "npn13g2":
            a1 = hbt_fixed_area(m, 1, 1)
            a4 = hbt_fixed_area(m, 4, 1)
        else:
            a1 = hbt_var_area(m, 1.0, 1, 1)
            a4 = hbt_var_area(m, 1.0, 4, 1)
        check(f"{dev}: area(nx=4) > area(nx=1)",
              a4 > a1, f"{a4:.2f} vs {a1:.2f}")

    # Resistors: longer → bigger
    for dev in ("rsil", "rppd", "rhigh"):
        model = db.get("resistors",{}).get(dev,{}).get("model",[])
        if not model: continue
        a1  = resistor_area(model, 0.5, 1.0, 1)
        a20 = resistor_area(model, 0.5, 20.0, 1)
        check(f"{dev}: area(L=20) > area(L=1)",
              a20 > a1, f"{a20:.2f} vs {a1:.2f}")


    # ═══════════════════════════════════════════════════════════════════════
    section("5. MULTIPLIER LINEARITY (m=2 → exactly 2× area)")

    for dev in ("sg13_lv_nmos", "sg13_hv_nmos"):
        m = get_mos(dev)
        if not m: continue
        Lmin = 0.13 if "lv" in dev else 0.45
        a1 = mosfet_area(m, 1.0, Lmin, 1, 1)
        a2 = mosfet_area(m, 1.0, Lmin, 1, 2)
        check(f"{dev}: m=2 gives exactly 2× area",
              abs(a2 - 2*a1) < 1e-9, f"{a2:.4f} vs 2×{a1:.4f}={2*a1:.4f}")

    cap_model = db.get("mim_caps",{}).get("cap_cmim",{}).get("model",{})
    if cap_model:
        a1 = cap_area(cap_model, 5.0, 5.0, 1)
        a2 = cap_area(cap_model, 5.0, 5.0, 2)
        check("cap_cmim: m=2 gives exactly 2× area",
              abs(a2 - 2*a1) < 1e-9, f"{a2:.4f} vs 2×{a1:.4f}={2*a1:.4f}")


    # ═══════════════════════════════════════════════════════════════════════
    section("6. CROSS-DEVICE ORDERING (physical expectations)")

    lv_m  = get_mos("sg13_lv_nmos")
    hv_m  = get_mos("sg13_hv_nmos")
    lvp_m = get_mos("sg13_lv_pmos")
    hvp_m = get_mos("sg13_hv_pmos")

    if lv_m and hv_m:
        # HV at its Lmin vs LV at its Lmin — HV must be larger (bigger design rules)
        a_lv = mosfet_area(lv_m,  1.0, 0.13, 1, 1)
        a_hv = mosfet_area(hv_m,  1.0, 0.45, 1, 1)
        check("sg13_hv_nmos(L=0.45) > sg13_lv_nmos(L=0.13) at W=1um",
              a_hv > a_lv, f"HV={a_hv:.2f} vs LV={a_lv:.2f}")

    if lvp_m and lv_m:
        # LV PMOS usually slightly taller than LV NMOS (n-well overhead)
        a_n = mosfet_area(lv_m,  1.0, 0.13, 1, 1)
        a_p = mosfet_area(lvp_m, 1.0, 0.13, 1, 1)
        check("sg13_lv_pmos >= sg13_lv_nmos at same W/L (n-well overhead)",
              a_p >= a_n * 0.95,   # allow 5% tolerance
              f"PMOS={a_p:.2f} vs NMOS={a_n:.2f}")

    # Cap: larger plate → larger area (monotone in both W and L)
    if cap_model:
        a_small = cap_area(cap_model, 2.0,  2.0,  1)
        a_large = cap_area(cap_model, 10.0, 10.0, 1)
        check("cap_cmim: 10×10 > 2×2",
              a_large > a_small, f"{a_large:.2f} vs {a_small:.2f}")

    # MIM border must be positive and plausible (0.1 – 5.0 µm)
    if cap_model:
        bdr = cap_model.get("border_um", -1)
        check(f"cap_cmim: border_um={bdr:.3f} is in [0.1, 5.0]",
              0.1 <= bdr <= 5.0,
              f"border={bdr:.3f} — unexpected value")

    # PDK REALITY (confirmed by direct Magic measurement, 2026-07-18):
    # npn13g2l emitter pitch s=2.80µm; npn13g2v pitch s=2.34µm.
    # At l≈1µm, npn13g2l is LARGER than npn13g2v (wider pitch dominates).
    # The model must reflect this — it is correct PDK behavior.
    npnl = db.get("hbts",{}).get("npn13g2l",{}).get("model",{})
    npnv = db.get("hbts",{}).get("npn13g2v",{}).get("model",{})
    if npnl and npnv:
        al = hbt_var_area(npnl, 1.0, 1, 1)
        av = hbt_var_area(npnv, 1.0, 1, 1)
        check("npn13g2l > npn13g2v at l=1um, nx=1 (PDK: s=2.80 > s=2.34)",
              al > av, f"l={al:.2f} vs v={av:.2f}")


    # ═══════════════════════════════════════════════════════════════════════
    section("7. ESTIMATOR INTEGRATION TEST")

    if estimator is None:
        print("  (skipped — estimator not found)")
    else:
        # Write a sample IHP130 netlist
        sample_spice = textwrap.dedent("""\
            ** IHP130 validation netlist — known device mix
            .subckt val_test vdd vss

            * LV NMOS  W=2um L=0.13um ng=1 m=1
            XM1 d g s b sg13_lv_nmos W=2e-6 L=0.13e-6 ng=1 m=1
            * LV PMOS  W=4um L=0.5um ng=2 m=1
            XM2 d g s b sg13_lv_pmos W=4e-6 L=0.5e-6 ng=2 m=1
            * HV NMOS  W=1um L=0.45um ng=1 m=1
            XM3 d g s b sg13_hv_nmos W=1e-6 L=0.45e-6 ng=1 m=1
            * HV PMOS  W=2um L=0.4um ng=1 m=1
            XM4 d g s b sg13_hv_pmos W=2e-6 L=0.40e-6 ng=1 m=1
            * rsil   W=0.5um L=10um m=1
            XR1 n1 n2 rsil w=0.5e-6 l=10e-6 m=1 b=0
            * rppd   W=0.5um L=5um m=1
            XR2 n1 n2 rppd w=0.5e-6 l=5e-6 m=1 b=0
            * rhigh  W=0.5um L=20um m=1
            XR3 n1 n2 rhigh w=0.5e-6 l=20e-6 m=1 b=0
            * cap_cmim W=5um L=5um m=1
            XC1 vp vn cap_cmim W=5e-6 L=5e-6 m=1
            * npn13g2 nx=2 m=1
            XQ1 c b e npn13g2 Nx=2 m=1
            * npn13g2l le=1.5um nx=2 m=1
            XQ2 c b e npn13g2l le=1.5e-6 Nx=2 m=1
            * npn13g2v le=2um nx=3 m=1
            XQ3 c b e npn13g2v le=2e-6 Nx=3 m=1

            .ends val_test
            .end
        """)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".spice",
                                         delete=False, prefix="ihp130_val_") as tf:
            tf.write(sample_spice)
            spice_path = tf.name

        import subprocess
        cmd = [sys.executable, str(estimator),
               "--netlist", spice_path,
               "--db", str(db_path),
               "--verbose"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        os.unlink(spice_path)

        out = result.stdout + result.stderr

        # 7a: exit code 0
        check("Estimator exits cleanly (exit code 0)",
              result.returncode == 0,
              f"exit code: {result.returncode}\nSTDERR: {result.stderr[:300]}")

        # 7b: all 11 instances costed, 0 skipped
        import re
        m_parsed = re.search(r"Parsed\s+(\d+)\s+instances.*?costed\s+(\d+).*?skipped\s+(\d+)", out)
        if m_parsed:
            parsed, costed, skipped = int(m_parsed.group(1)), int(m_parsed.group(2)), int(m_parsed.group(3))
            check(f"All 11 instances costed, 0 skipped (got {costed}/{parsed}, skip={skipped})",
                  costed == 11 and skipped == 0,
                  f"parsed={parsed}, costed={costed}, skipped={skipped}")
        else:
            check("Parsed/costed/skipped line found in output",
                  False, f"Output:\n{out[:400]}")

        # 7c: TOTAL ESTIMATED AREA line present and reasonable
        m_area = re.search(r"TOTAL ESTIMATED AREA\s+([\d.]+)\s+µm", out)
        if m_area:
            total = float(m_area.group(1))
            # 11 devices × routing overhead: should be 100–3000 µm²
            check(f"Total estimated area={total:.1f}µm² is plausible (100–3000µm²)",
                  100 <= total <= 3000,
                  f"area={total:.1f} — check coefficients")
        else:
            check("TOTAL ESTIMATED AREA line found", False, out[:400])

        # 7d: no "[WARN] PLACEHOLDER" in output (real DB should suppress it)
        check("No PLACEHOLDER warning in estimator output",
              "PLACEHOLDER" not in out,
              "Estimator is still reading placeholder DB — check --db path")


    # ═══════════════════════════════════════════════════════════════════════
    section("8. COEFFICIENT REASONABLENESS SPOT-CHECKS")

    # These are tight sanity checks based on IHP130 DRC rules.
    # If a coefficient is way off, it indicates a fitting/parsing bug.

    lv_nmos = db.get("mosfets",{}).get("sg13_lv_nmos",{}).get("model",{})
    if lv_nmos:
        # bh (height offset) must be > 0 (well contact overhead always > 0)
        check("sg13_lv_nmos: bh > 0", lv_nmos.get("bh",0) > 0,
              f"bh={lv_nmos.get('bh')}")
        # ah (height per µm W) must be in [0.5, 3.0] — physical scaling
        check("sg13_lv_nmos: ah in [0.5, 3.0]",
              0.5 <= lv_nmos.get("ah",0) <= 3.0,
              f"ah={lv_nmos.get('ah')} — unexpected scaling factor")
        # cw (width offset, 0-finger overhead) must be > 0
        check("sg13_lv_nmos: cw > 0 (gate overhead exists)",
              lv_nmos.get("cw",0) > 0, f"cw={lv_nmos.get('cw')}")

    # Cap border must be < min(W, L) to make physical sense
    if cap_model:
        bdr = cap_model.get("border_um", 0)
        check("cap_cmim: border_um < 2.0µm (cap min=2µm, border must be < that)",
              bdr < 2.0, f"border={bdr:.3f} — larger than the minimum cap dimension!")

    # Resistors: slope must be positive (area grows with length)
    for dev in ("rsil", "rppd", "rhigh"):
        model = db.get("resistors",{}).get(dev,{}).get("model",[])
        for entry in model:
            check(f"{dev}(W={entry.get('w')}): slope > 0",
                  entry.get("slope",0) > 0,
                  f"slope={entry.get('slope')} — negative area growth!")
            check(f"{dev}(W={entry.get('w')}): intercept > 0",
                  entry.get("intercept",0) > 0,
                  f"intercept={entry.get('intercept')} — zero-length has no area?")

    # HBT: fixed_h for npn13g2 should be 2–20 µm
    npn = db.get("hbts",{}).get("npn13g2",{}).get("model",{})
    if npn:
        fh = npn.get("fixed_h",0)
        check(f"npn13g2: fixed_h={fh:.2f} in [2, 20] µm",
              2.0 <= fh <= 20.0, f"fixed_h={fh}")
        check(f"npn13g2: aw > 0 (width grows with nx)",
              npn.get("aw",0) > 0, f"aw={npn.get('aw')}")


    # ═══════════════════════════════════════════════════════════════════════
    section("SUMMARY")

    n_pass  = sum(1 for r in results if r[0] == PASS)
    n_fail  = sum(1 for r in results if r[0] == FAIL)
    n_total = len(results)

    print(f"\n  Total : {n_total}")
    print(f"  Passed: {n_pass}")
    print(f"  Failed: {n_fail}")
    print()

    if n_fail == 0:
        print("  🎉  ALL TESTS PASSED — database looks real and physically sane.\n")
        sys.exit(0)
    else:
        print(f"  ❌  {n_fail} TEST(S) FAILED — see details above.\n")
        print("  Failed tests:")
        for tag, name, detail in results:
            if tag == FAIL:
                print(f"    • {name}")
                if detail:
                    for line in detail.splitlines():
                        print(f"        {line}")
        sys.exit(1)


if __name__ == "__main__":
    main()
