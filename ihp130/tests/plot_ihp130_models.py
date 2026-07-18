#!/usr/bin/env python3
"""
plot_ihp130_models.py
=====================
Generate device model plots for the IHP130 SG13G2 area estimator.
Mirrors the style of the SKY130 plot_models.py:
  - Parity scatter plot: measured vs predicted (blue circles, k-- perfect-fit line)
  - Residual bar plot (% error per sweep point)
  - Model curve overlay on measured data
  - R² annotated on each plot

Run from the ihp130/ directory:
    python3 tests/plot_ihp130_models.py

Plots are saved to ../reports/plots/
"""

import json
import sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

# ─── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
IHP130_DIR = SCRIPT_DIR.parent          # ihp130/
REPO_ROOT  = IHP130_DIR.parent          # repo root
DB_PATH    = IHP130_DIR / "device_db.json"
PLOTS_DIR  = REPO_ROOT / "reports" / "plots"
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

with open(DB_PATH) as f:
    db = json.load(f)

# ─── Style (matching SKY130 plots) ────────────────────────────────────────────
BLUE   = "#2563EB"
RED    = "#DC2626"
GREEN  = "#16A34A"
ORANGE = "#EA580C"
GRAY   = "#6B7280"
GRID_KW = dict(linestyle=":", alpha=0.6, color="#D1D5DB")
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
})

# ─── Helper: R² ───────────────────────────────────────────────────────────────
def r2_score(y_true, y_pred):
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    ss_res = np.sum((y_true - y_pred)**2)
    ss_tot = np.sum((y_true - y_true.mean())**2)
    return 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 1.0

def save(name):
    path = PLOTS_DIR / name
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {path.name}")

# ══════════════════════════════════════════════════════════════════════════════
# 1. MOSFET PLOTS
# ══════════════════════════════════════════════════════════════════════════════
def pred_mosfet(m, p):
    W  = p.get("w", 0)
    L  = p.get("l", 0)
    nf = p.get("nf", 1)
    mu = p.get("m", 1)
    return (m["ah"]*W + m["bh"]) * (m["aw"]*L*nf + m["bw"]*nf + m["cw"]) * mu

for dev, data in db["mosfets"].items():
    pts = data.get("sweep", [])
    if len(pts) < 3:
        continue
    m   = data["model"]
    A   = np.array([p["area_um2"] for p in pts])
    P   = np.array([pred_mosfet(m, p) for p in pts])
    r2  = r2_score(A, P)
    err = (P - A) / A * 100

    fig = plt.figure(figsize=(12, 5))
    gs  = gridspec.GridSpec(1, 2, figure=fig, wspace=0.35)

    # ── Left: Parity plot ──────────────────────────────────────────────────
    ax1 = fig.add_subplot(gs[0])
    lim = [min(A.min(), P.min()) * 0.9, max(A.max(), P.max()) * 1.1]
    ax1.plot(lim, lim, "k--", alpha=0.5, lw=1.2, label="Perfect Fit")
    ax1.scatter(A, P, color=BLUE, marker="o", s=45, alpha=0.75,
                edgecolors="white", lw=0.5, label="Bounding-Box Model")
    ax1.set_xlim(lim); ax1.set_ylim(lim)
    ax1.set_xlabel("Magic Measured Area (µm²)")
    ax1.set_ylabel("Model Predicted Area (µm²)")
    ax1.set_title(f"{dev}\nParity Plot  |  R²={r2:.6f}", fontsize=10, fontweight="bold")
    ax1.legend(fontsize=8)
    ax1.grid(**GRID_KW)
    ax1.text(0.97, 0.05, f"R² = {r2:.6f}", transform=ax1.transAxes,
             ha="right", va="bottom", fontsize=9, color=BLUE,
             bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=BLUE, alpha=0.8))

    # ── Right: Residual % bar chart ─────────────────────────────────────────
    ax2 = fig.add_subplot(gs[1])
    colors = [RED if abs(e) > 2 else BLUE for e in err]
    ax2.bar(range(len(err)), err, color=colors, alpha=0.75, width=0.7)
    ax2.axhline(0, color="black", lw=0.8)
    ax2.axhline(+2, color=ORANGE, lw=0.8, ls="--", alpha=0.6, label="±2% band")
    ax2.axhline(-2, color=ORANGE, lw=0.8, ls="--", alpha=0.6)
    ax2.set_xlabel("Sweep Point Index")
    ax2.set_ylabel("Residual Error (%)")
    ax2.set_title(f"{dev}\nResidual Error  |  max={np.abs(err).max():.2f}%", fontsize=10, fontweight="bold")
    ax2.legend(fontsize=8)
    ax2.grid(**GRID_KW)

    fig.suptitle(f"IHP130 SG13G2 — {dev} Area Model", fontsize=12, fontweight="bold", y=1.01)
    save(f"plot_{dev}.png")
    print(f"    {dev}: R²={r2:.6f}  max_err={np.abs(err).max():.2f}%  pts={len(pts)}")

# ══════════════════════════════════════════════════════════════════════════════
# 2. RESISTOR PLOTS
# ══════════════════════════════════════════════════════════════════════════════
def pred_resistor(model_entries, p):
    W = p.get("w", 0)
    L = p.get("l", 0)
    entry = min(model_entries, key=lambda e: abs(e["w"] - W))
    return entry["slope"] * L + entry["intercept"]

RES_COLORS = {"0.5": BLUE, "1.0": GREEN, "2.0": ORANGE}

for dev, data in db["resistors"].items():
    pts    = data.get("sweep", [])
    models = data.get("model", [])
    if not pts or not models:
        continue

    A  = np.array([p["area_um2"] for p in pts])
    P  = np.array([pred_resistor(models, p) for p in pts])
    r2 = r2_score(A, P)
    err = (P - A) / A * 100

    fig = plt.figure(figsize=(14, 5))
    gs  = gridspec.GridSpec(1, 3, figure=fig, wspace=0.35)

    # ── Left: Area vs L per width ──────────────────────────────────────────
    ax1 = fig.add_subplot(gs[0])
    by_w = {}
    for p in pts:
        w = p.get("w", 0)
        by_w.setdefault(w, []).append(p)

    for w, wpts in sorted(by_w.items()):
        L_arr = np.array([p.get("l", 0) for p in wpts])
        A_arr = np.array([p["area_um2"]  for p in wpts])
        col   = RES_COLORS.get(str(w), GRAY)
        entry = min(models, key=lambda e: abs(e["w"] - w))
        L_line = np.linspace(L_arr.min(), L_arr.max(), 80)
        P_line = entry["slope"] * L_line + entry["intercept"]
        ax1.scatter(L_arr, A_arr, color=col, marker="o", s=50, alpha=0.85,
                    edgecolors="white", lw=0.5, zorder=3, label=f"W={w}µm (meas.)")
        ax1.plot(L_line, P_line, color=col, lw=1.6, ls="-", alpha=0.7)

    ax1.set_xlabel("Length L (µm)")
    ax1.set_ylabel("Area (µm²)")
    ax1.set_title(f"{dev}\nArea vs L per Width", fontsize=10, fontweight="bold")
    ax1.legend(fontsize=8)
    ax1.grid(**GRID_KW)

    # ── Middle: Parity plot ────────────────────────────────────────────────
    ax2 = fig.add_subplot(gs[1])
    lim = [min(A.min(), P.min()) * 0.9, max(A.max(), P.max()) * 1.1]
    ax2.plot(lim, lim, "k--", alpha=0.5, lw=1.2, label="Perfect Fit")
    ax2.scatter(A, P, color=BLUE, marker="o", s=45, alpha=0.75,
                edgecolors="white", lw=0.5)
    ax2.set_xlim(lim); ax2.set_ylim(lim)
    ax2.set_xlabel("Measured Area (µm²)")
    ax2.set_ylabel("Predicted Area (µm²)")
    ax2.set_title(f"{dev}\nParity  |  R²={r2:.6f}", fontsize=10, fontweight="bold")
    ax2.text(0.97, 0.05, f"R² = {r2:.6f}", transform=ax2.transAxes,
             ha="right", va="bottom", fontsize=9, color=BLUE,
             bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=BLUE, alpha=0.8))
    ax2.legend(fontsize=8)
    ax2.grid(**GRID_KW)

    # ── Right: Residual % ──────────────────────────────────────────────────
    ax3 = fig.add_subplot(gs[2])
    colors = [RED if abs(e) > 1 else BLUE for e in err]
    ax3.bar(range(len(err)), err, color=colors, alpha=0.75, width=0.7)
    ax3.axhline(0, color="black", lw=0.8)
    ax3.axhline(+1, color=ORANGE, lw=0.8, ls="--", alpha=0.6, label="±1% band")
    ax3.axhline(-1, color=ORANGE, lw=0.8, ls="--", alpha=0.6)
    ax3.set_xlabel("Sweep Point Index")
    ax3.set_ylabel("Residual Error (%)")
    ax3.set_title(f"{dev}\nResidual Error", fontsize=10, fontweight="bold")
    ax3.legend(fontsize=8)
    ax3.grid(**GRID_KW)

    fig.suptitle(f"IHP130 SG13G2 — {dev} Resistor Model", fontsize=12, fontweight="bold", y=1.01)
    save(f"plot_{dev}.png")
    print(f"    {dev}: R²={r2:.6f}  max_err={np.abs(err).max():.2f}%  pts={len(pts)}")

# ══════════════════════════════════════════════════════════════════════════════
# 3. MIM CAP PLOT
# ══════════════════════════════════════════════════════════════════════════════
def pred_cap(m, p):
    W = p.get("w", 0); L = p.get("l", 0); b = m["border_um"]
    return (W + 2*b) * (L + 2*b)

for dev, data in db["mim_caps"].items():
    pts = data.get("sweep", [])
    m   = data.get("model", {})
    if not pts or not m:
        continue

    A   = np.array([p["area_um2"] for p in pts])
    P   = np.array([pred_cap(m, p) for p in pts])
    r2  = r2_score(A, P)
    err = (P - A) / A * 100
    b   = m["border_um"]

    fig = plt.figure(figsize=(12, 5))
    gs  = gridspec.GridSpec(1, 2, figure=fig, wspace=0.35)

    # ── Left: Area vs plate area with border model ─────────────────────────
    ax1 = fig.add_subplot(gs[0])
    plate = np.array([p.get("w",0)*p.get("l",0) for p in pts])
    ax1.scatter(plate, A, color=BLUE, marker="o", s=60, alpha=0.85,
                edgecolors="white", lw=0.5, zorder=3, label="Measured area")
    plate_line = np.linspace(plate.min(), plate.max(), 100)
    # Approximate: for square caps, W=L, plate=W², total=(W+2b)²
    # Use a representative aspect ratio — just show model as function of plate
    W_vals = np.sqrt(plate_line)
    P_line = (W_vals + 2*b) * (W_vals + 2*b)
    ax1.plot(plate_line, P_line, color=RED, lw=1.8, ls="-", alpha=0.8,
             label=f"Model: (W+{2*b:.3f})·(L+{2*b:.3f})")
    ax1.set_xlabel("Plate Area W·L (µm²)")
    ax1.set_ylabel("Total Cell Area (µm²)")
    ax1.set_title(f"{dev}\nArea vs Plate Area  |  border={b:.4f}µm", fontsize=10, fontweight="bold")
    ax1.legend(fontsize=8)
    ax1.grid(**GRID_KW)

    # ── Right: Parity plot ─────────────────────────────────────────────────
    ax2 = fig.add_subplot(gs[1])
    lim = [min(A.min(), P.min()) * 0.9, max(A.max(), P.max()) * 1.1]
    ax2.plot(lim, lim, "k--", alpha=0.5, lw=1.2, label="Perfect Fit")
    ax2.scatter(A, P, color=BLUE, marker="o", s=60, alpha=0.8,
                edgecolors="white", lw=0.5)
    ax2.set_xlim(lim); ax2.set_ylim(lim)
    ax2.set_xlabel("Measured Area (µm²)")
    ax2.set_ylabel("Predicted Area (µm²)")
    ax2.set_title(f"{dev}\nParity  |  R²={r2:.6f}", fontsize=10, fontweight="bold")
    ax2.text(0.97, 0.05, f"R² = {r2:.6f}\nborder = {b:.4f}µm",
             transform=ax2.transAxes, ha="right", va="bottom", fontsize=9, color=BLUE,
             bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=BLUE, alpha=0.8))
    ax2.legend(fontsize=8)
    ax2.grid(**GRID_KW)

    fig.suptitle(f"IHP130 SG13G2 — {dev} Capacitor Model", fontsize=12, fontweight="bold", y=1.01)
    save(f"plot_{dev}.png")
    print(f"    {dev}: R²={r2:.6f}  border={b:.4f}µm  pts={len(pts)}")

# ══════════════════════════════════════════════════════════════════════════════
# 4. HBT PLOTS
# ══════════════════════════════════════════════════════════════════════════════
def pred_hbt_fixed(m, p):
    nx = p.get("nx", p.get("nf", 1))
    mu = p.get("m", 1)
    return m["fixed_h"] * (m["aw"]*nx + m["bw"]) * mu

def pred_hbt_var(m, p):
    l  = p.get("l", p.get("L", 0))
    nx = p.get("nx", p.get("nf", 1))
    mu = p.get("m", 1)
    return (m["ah"]*l + m["bh"]) * (m["aw"]*nx + m["bw"]) * mu

for dev, data in db["hbts"].items():
    pts = data.get("sweep", [])
    m   = data.get("model", {})
    if not pts or not m:
        continue

    is_fixed = "fixed_h" in m
    A  = np.array([p["area_um2"] for p in pts])
    P  = np.array([(pred_hbt_fixed(m, p) if is_fixed else pred_hbt_var(m, p)) for p in pts])
    r2 = r2_score(A, P)
    err = (P - A) / A * 100

    fig = plt.figure(figsize=(14, 5))
    gs  = gridspec.GridSpec(1, 3, figure=fig, wspace=0.38)

    # ── Left: Area vs nx (for fixed) or vs l (for var) ────────────────────
    ax1 = fig.add_subplot(gs[0])
    if is_fixed:
        nx_arr = np.array([p.get("nx", p.get("nf", 1)) for p in pts])
        ax1.scatter(nx_arr, A, color=BLUE, marker="o", s=60, alpha=0.85,
                    edgecolors="white", lw=0.5, zorder=3, label="Measured")
        nx_line = np.linspace(nx_arr.min(), nx_arr.max(), 80)
        P_line  = m["fixed_h"] * (m["aw"]*nx_line + m["bw"])
        ax1.plot(nx_line, P_line, color=RED, lw=1.8, alpha=0.8, label="Model")
        ax1.set_xlabel("Number of Emitters nx")
        ax1.set_title(f"{dev}\nArea vs nx", fontsize=10, fontweight="bold")
    else:
        l_arr  = np.array([p.get("l", p.get("L", 0)) for p in pts])
        nx_arr = np.array([p.get("nx", p.get("nf", 1)) for p in pts])
        sc = ax1.scatter(nx_arr, A, c=l_arr, cmap="viridis", marker="o", s=60,
                         alpha=0.85, edgecolors="white", lw=0.5, zorder=3)
        plt.colorbar(sc, ax=ax1, label="Emitter length l (µm)")
        # Model curves per l value
        for l_val in sorted(set(l_arr)):
            nx_line = np.linspace(nx_arr.min(), nx_arr.max(), 80)
            P_line  = (m["ah"]*l_val + m["bh"]) * (m["aw"]*nx_line + m["bw"])
            ax1.plot(nx_line, P_line, lw=1.5, alpha=0.6)
        ax1.set_xlabel("Number of Emitters nx")
        ax1.set_title(f"{dev}\nArea vs nx (color=l)", fontsize=10, fontweight="bold")

    ax1.set_ylabel("Area (µm²)")
    ax1.legend(fontsize=8)
    ax1.grid(**GRID_KW)

    # ── Middle: Parity plot ────────────────────────────────────────────────
    ax2 = fig.add_subplot(gs[1])
    lim = [min(A.min(), P.min()) * 0.9, max(A.max(), P.max()) * 1.1]
    ax2.plot(lim, lim, "k--", alpha=0.5, lw=1.2, label="Perfect Fit")
    ax2.scatter(A, P, color=BLUE, marker="o", s=50, alpha=0.8,
                edgecolors="white", lw=0.5)
    ax2.set_xlim(lim); ax2.set_ylim(lim)
    ax2.set_xlabel("Measured Area (µm²)")
    ax2.set_ylabel("Predicted Area (µm²)")
    ax2.set_title(f"{dev}\nParity  |  R²={r2:.6f}", fontsize=10, fontweight="bold")
    ax2.text(0.97, 0.05, f"R² = {r2:.6f}", transform=ax2.transAxes,
             ha="right", va="bottom", fontsize=9, color=BLUE,
             bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=BLUE, alpha=0.8))
    ax2.legend(fontsize=8)
    ax2.grid(**GRID_KW)

    # ── Right: Residual % ──────────────────────────────────────────────────
    ax3 = fig.add_subplot(gs[2])
    colors = [RED if abs(e) > 2 else BLUE for e in err]
    ax3.bar(range(len(err)), err, color=colors, alpha=0.75, width=0.7)
    ax3.axhline(0, color="black", lw=0.8)
    ax3.axhline(+2, color=ORANGE, lw=0.8, ls="--", alpha=0.6, label="±2% band")
    ax3.axhline(-2, color=ORANGE, lw=0.8, ls="--", alpha=0.6)
    ax3.set_xlabel("Sweep Point Index")
    ax3.set_ylabel("Residual Error (%)")
    ax3.set_title(f"{dev}\nResidual Error", fontsize=10, fontweight="bold")
    ax3.legend(fontsize=8)
    ax3.grid(**GRID_KW)

    fig.suptitle(f"IHP130 SG13G2 — {dev} HBT Model", fontsize=12, fontweight="bold", y=1.01)
    save(f"plot_{dev}.png")
    print(f"    {dev}: R²={r2:.6f}  max_err={np.abs(err).max():.2f}%  pts={len(pts)}")

# ══════════════════════════════════════════════════════════════════════════════
# 5. MODEL COMPARISON SUMMARY (all 11 devices)
# ══════════════════════════════════════════════════════════════════════════════
print("\nGenerating model_comparison_ihp130.png ...")

all_devs    = []
all_r2      = []
all_maxerr  = []
all_pts     = []

def collect_stats(dev, A, P, n):
    r2  = r2_score(A, P)
    err = np.abs((P - A) / A * 100)
    all_devs.append(dev)
    all_r2.append(r2)
    all_maxerr.append(err.max())
    all_pts.append(n)

for dev, data in db["mosfets"].items():
    pts = data.get("sweep", [])
    if not pts: continue
    m = data["model"]
    A = np.array([p["area_um2"] for p in pts])
    P = np.array([pred_mosfet(m, p) for p in pts])
    collect_stats(dev, A, P, len(pts))

for dev, data in db["resistors"].items():
    pts = data.get("sweep", [])
    mdl = data.get("model", [])
    if not pts or not mdl: continue
    A = np.array([p["area_um2"] for p in pts])
    P = np.array([pred_resistor(mdl, p) for p in pts])
    collect_stats(dev, A, P, len(pts))

for dev, data in db["mim_caps"].items():
    pts = data.get("sweep", [])
    m   = data.get("model", {})
    if not pts or not m: continue
    A = np.array([p["area_um2"] for p in pts])
    P = np.array([pred_cap(m, p) for p in pts])
    collect_stats(dev, A, P, len(pts))

for dev, data in db["hbts"].items():
    pts = data.get("sweep", [])
    m   = data.get("model", {})
    if not pts or not m: continue
    is_f = "fixed_h" in m
    A = np.array([p["area_um2"] for p in pts])
    P = np.array([(pred_hbt_fixed(m, p) if is_f else pred_hbt_var(m, p)) for p in pts])
    collect_stats(dev, A, P, len(pts))

fig, axes = plt.subplots(1, 2, figsize=(16, 6))
x = np.arange(len(all_devs))
short_labels = [d.replace("sg13_", "").replace("_nmos","_N").replace("_pmos","_P")
                .replace("npn13g2","npn") for d in all_devs]

# ── Left: R² per device ───────────────────────────────────────────────────
ax = axes[0]
bars = ax.bar(x, all_r2, color=BLUE, alpha=0.8, width=0.65, edgecolor="white")
ax.axhline(0.990, color=RED, ls="--", lw=1.2, alpha=0.7, label="R²=0.990 threshold")
ax.set_xticks(x)
ax.set_xticklabels(short_labels, rotation=38, ha="right", fontsize=8)
ax.set_ylim(0.96, 1.002)
ax.set_ylabel("R² (goodness of fit)")
ax.set_title("Model R² — All 11 IHP130 Devices", fontsize=11, fontweight="bold")
for bar, r in zip(bars, all_r2):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.0002,
            f"{r:.4f}", ha="center", va="bottom", fontsize=6.5, color="#1E3A5F")
ax.legend(fontsize=9)
ax.grid(axis="y", **GRID_KW)

# ── Right: Max error % per device ─────────────────────────────────────────
ax = axes[1]
errcols = [RED if e > 2 else BLUE for e in all_maxerr]
bars = ax.bar(x, all_maxerr, color=errcols, alpha=0.8, width=0.65, edgecolor="white")
ax.axhline(2.0, color=RED, ls="--", lw=1.2, alpha=0.7, label="2% threshold")
ax.set_xticks(x)
ax.set_xticklabels(short_labels, rotation=38, ha="right", fontsize=8)
ax.set_ylabel("Max Point Error (%)")
ax.set_title("Max Prediction Error — All 11 IHP130 Devices", fontsize=11, fontweight="bold")
for bar, e in zip(bars, all_maxerr):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.005,
            f"{e:.2f}%", ha="center", va="bottom", fontsize=7, color="#4B0000")
ax.legend(fontsize=9)
ax.grid(axis="y", **GRID_KW)

fig.suptitle("IHP130 SG13G2 — Model Performance Summary Across All 11 Devices",
             fontsize=13, fontweight="bold", y=1.01)
plt.tight_layout()
save("model_comparison_ihp130.png")

print(f"\nAll plots saved to: {PLOTS_DIR}")
print(f"Total devices plotted: {len(all_devs)}")
