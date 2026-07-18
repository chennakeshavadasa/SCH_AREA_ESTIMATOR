#!/usr/bin/env python3
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

# Paths
SCRIPT_DIR = Path(__file__).resolve().parent
IHP130_DIR = SCRIPT_DIR.parent
REPO_ROOT = IHP130_DIR.parent
DB_PATH = IHP130_DIR / "device_db.json"
PLOTS_DIR = REPO_ROOT / "reports" / "plots"
PLOTS_DIR.mkdir(parents=True, exist_ok=True)

with open(DB_PATH) as f:
    db = json.load(f)

# Styling
try:
    plt.style.use('seaborn-v0_8-whitegrid')
except:
    pass

IHP_BLUE = "#1A5276"
SCATTER_ORANGE = "#E67E22"
SCATTER_KW = dict(color=SCATTER_ORANGE, marker='o', s=36, alpha=0.8, edgecolor='w', lw=0.5, zorder=3)
LINE_KW = dict(color=IHP_BLUE, lw=2, alpha=0.9, zorder=2)

def r2_score(y_true, y_pred):
    ss_res = np.sum((y_true - y_pred)**2)
    ss_tot = np.sum((y_true - np.mean(y_true))**2)
    return 1 - (ss_res / ss_tot) if ss_tot > 1e-12 else 1.0

# ════════════════════════════════════════════════════════════════════════
# FIG 1-4: MOSFETs
# ════════════════════════════════════════════════════════════════════════
for dev in ["sg13_lv_nmos", "sg13_lv_pmos", "sg13_hv_nmos", "sg13_hv_pmos"]:
    if dev not in db["mosfets"]: continue
    m = db["mosfets"][dev]["model"]
    pts = db["mosfets"][dev]["sweep"]
    
    W_vals = np.array([p["w"] for p in pts])
    L_vals = np.array([p["l"] for p in pts])
    nf_vals = np.array([p["nf"] for p in pts])
    area_vals = np.array([p["area_um2"] for p in pts])
    
    def mosfet_area(W, L, nf):
        return (m["ah"]*W + m["bh"]) * (m["aw"]*L*nf + m["bw"]*nf + m["cw"])

    fig, axes = plt.subplots(1, 3, figsize=(15, 5), dpi=150)
    fig.suptitle(f"{dev} Area Model", fontsize=14, fontweight='bold', color=IHP_BLUE)

    # 1. Area vs W (L=Lmin, nf=1)
    lmin = L_vals.min()
    mask1 = (np.isclose(L_vals, lmin)) & (nf_vals == 1)
    axes[0].scatter(W_vals[mask1], area_vals[mask1], label="Measured", **SCATTER_KW)
    w_line = np.linspace(W_vals[mask1].min(), W_vals[mask1].max(), 50)
    p_line = mosfet_area(w_line, lmin, 1)
    r2_1 = r2_score(area_vals[mask1], mosfet_area(W_vals[mask1], lmin, 1))
    axes[0].plot(w_line, p_line, label=f"Model (R²={r2_1:.4f})", **LINE_KW)
    axes[0].set_xlabel("Width W (µm)"); axes[0].set_ylabel("Area (µm²)")
    axes[0].set_title(f"Area vs W (L={lmin}µm, nf=1)")
    axes[0].legend()

    # 2. Area vs L (W=~2.0, nf=1)
    target_w = 2.0
    w_closest = W_vals[np.argmin(np.abs(W_vals - target_w))]
    mask2 = (np.isclose(W_vals, w_closest)) & (nf_vals == 1)
    if any(mask2):
        axes[1].scatter(L_vals[mask2], area_vals[mask2], **SCATTER_KW)
        l_line = np.linspace(L_vals[mask2].min(), L_vals[mask2].max(), 50)
        p_line = mosfet_area(w_closest, l_line, 1)
        r2_2 = r2_score(area_vals[mask2], mosfet_area(w_closest, L_vals[mask2], 1))
        axes[1].plot(l_line, p_line, label=f"Model (R²={r2_2:.4f})", **LINE_KW)
        axes[1].set_title(f"Area vs L (W={w_closest}µm, nf=1)")
        axes[1].legend()
    axes[1].set_xlabel("Length L (µm)"); axes[1].set_ylabel("Area (µm²)")

    # 3. Area vs nf (W=~2.0, L=Lmin)
    mask3 = (np.isclose(W_vals, w_closest)) & (np.isclose(L_vals, lmin))
    if any(mask3):
        axes[2].scatter(nf_vals[mask3], area_vals[mask3], **SCATTER_KW)
        nf_line = np.linspace(nf_vals[mask3].min(), nf_vals[mask3].max(), 50)
        p_line = mosfet_area(w_closest, lmin, nf_line)
        r2_3 = r2_score(area_vals[mask3], mosfet_area(w_closest, lmin, nf_vals[mask3]))
        axes[2].plot(nf_line, p_line, label=f"Model (R²={r2_3:.4f})", **LINE_KW)
        axes[2].set_title(f"Area vs nf (W={w_closest}µm, L={lmin}µm)")
        axes[2].legend()
    axes[2].set_xlabel("Fingers nf"); axes[2].set_ylabel("Area (µm²)")

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / f"ihp130_{dev}.png")
    plt.close()

# ════════════════════════════════════════════════════════════════════════
# FIG 5-7: Resistors
# ════════════════════════════════════════════════════════════════════════
colors = [IHP_BLUE, "#27AE60", "#8E44AD"]
for dev in ["rsil", "rppd", "rhigh"]:
    if dev not in db["resistors"]: continue
    models = db["resistors"][dev]["model"]
    pts = db["resistors"][dev]["sweep"]
    
    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
    fig.suptitle(f"{dev} Area Model", fontsize=14, fontweight='bold', color=IHP_BLUE)
    
    w_groups = {}
    for p in pts:
        w_groups.setdefault(p["w"], []).append(p)
    
    for i, (w, group) in enumerate(sorted(w_groups.items())):
        c = colors[i % len(colors)]
        L_vals = np.array([p["l"] for p in group])
        area_vals = np.array([p["area_um2"] for p in group])
        
        m_entry = next((m for m in models if np.isclose(m["w"], w)), None)
        if not m_entry: continue
        
        ax.scatter(L_vals, area_vals, color=SCATTER_ORANGE, marker='o', s=36, edgecolor='w', zorder=3)
        l_line = np.linspace(L_vals.min(), L_vals.max(), 50)
        p_line = m_entry["slope"] * l_line + m_entry["intercept"]
        
        preds = m_entry["slope"] * L_vals + m_entry["intercept"]
        r2 = r2_score(area_vals, preds)
        ax.plot(l_line, p_line, color=c, lw=2, label=f"W={w}µm (R²={r2:.4f})", zorder=2)
        
    ax.set_xlabel("Length L (µm)")
    ax.set_ylabel("Area (µm²)")
    ax.legend()
    plt.tight_layout()
    plt.savefig(PLOTS_DIR / f"ihp130_{dev}.png")
    plt.close()

# ════════════════════════════════════════════════════════════════════════
# FIG 8: MIM Capacitor
# ════════════════════════════════════════════════════════════════════════
dev = "cap_cmim"
if dev in db["mim_caps"]:
    m = db["mim_caps"][dev]["model"]
    pts = db["mim_caps"][dev]["sweep"]
    b = m["border_um"]
    
    W_vals = np.array([p["w"] for p in pts])
    L_vals = np.array([p["l"] for p in pts])
    area_vals = np.array([p["area_um2"] for p in pts])
    
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), dpi=150)
    fig.suptitle(f"{dev} Area Model (border_um={b:.4f})", fontsize=14, fontweight='bold', color=IHP_BLUE)
    
    # Left: vs W (L=5.0)
    l_target = 5.0
    mask1 = np.isclose(L_vals, l_target)
    if any(mask1):
        axes[0].scatter(W_vals[mask1], area_vals[mask1], label="Measured", **SCATTER_KW)
        w_line = np.linspace(W_vals[mask1].min(), W_vals[mask1].max(), 50)
        p_line = (w_line + 2*b) * (l_target + 2*b)
        axes[0].plot(w_line, p_line, label="Model", **LINE_KW)
        axes[0].set_title(f"Area vs W (L={l_target}µm)"); axes[0].set_xlabel("W (µm)"); axes[0].set_ylabel("Area (µm²)")
        axes[0].legend()
        
    # Right: vs L (W=5.0)
    w_target = 5.0
    mask2 = np.isclose(W_vals, w_target)
    if any(mask2):
        axes[1].scatter(L_vals[mask2], area_vals[mask2], label="Measured", **SCATTER_KW)
        l_line = np.linspace(L_vals[mask2].min(), L_vals[mask2].max(), 50)
        p_line = (w_target + 2*b) * (l_line + 2*b)
        axes[1].plot(l_line, p_line, label="Model", **LINE_KW)
        axes[1].set_title(f"Area vs L (W={w_target}µm)"); axes[1].set_xlabel("L (µm)")
        axes[1].legend()

    plt.tight_layout()
    plt.savefig(PLOTS_DIR / f"ihp130_{dev}.png")
    plt.close()

# ════════════════════════════════════════════════════════════════════════
# FIG 9: HBT Fixed (npn13g2)
# ════════════════════════════════════════════════════════════════════════
dev = "npn13g2"
if dev in db["hbts"]:
    m = db["hbts"][dev]["model"]
    pts = db["hbts"][dev]["sweep"]
    nx_vals = np.array([p["nx"] for p in pts])
    area_vals = np.array([p["area_um2"] for p in pts])
    
    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
    fig.suptitle(f"{dev} Area Model", fontsize=14, fontweight='bold', color=IHP_BLUE)
    
    ax.scatter(nx_vals, area_vals, label="Measured", **SCATTER_KW)
    nx_line = np.linspace(nx_vals.min(), nx_vals.max(), 50)
    p_line = m["fixed_h"] * (m["aw"] * nx_line + m["bw"])
    r2 = r2_score(area_vals, m["fixed_h"] * (m["aw"] * nx_vals + m["bw"]))
    ax.plot(nx_line, p_line, label=f"Model (R²={r2:.4f})", **LINE_KW)
    
    textstr = f"fixed_h = {m['fixed_h']:.4f}\naw = {m['aw']:.4f}\nbw = {m['bw']:.4f}"
    ax.text(0.05, 0.95, textstr, transform=ax.transAxes, va='top', bbox=dict(facecolor='white', alpha=0.8, ec=IHP_BLUE))
    ax.set_xlabel("nx"); ax.set_ylabel("Area (µm²)")
    ax.legend()
    plt.tight_layout()
    plt.savefig(PLOTS_DIR / f"ihp130_{dev}.png")
    plt.close()

# ════════════════════════════════════════════════════════════════════════
# FIG 10-11: HBT Variable
# ════════════════════════════════════════════════════════════════════════
for dev in ["npn13g2l", "npn13g2v"]:
    if dev not in db["hbts"]: continue
    m = db["hbts"][dev]["model"]
    pts = db["hbts"][dev]["sweep"]
    
    l_vals = np.array([p["l"] for p in pts])
    nx_vals = np.array([p["nx"] for p in pts])
    area_vals = np.array([p["area_um2"] for p in pts])
    
    def hbt_area(l, nx):
        return (m["ah"]*l + m["bh"]) * (m["aw"]*nx + m["bw"])

    fig = plt.figure(figsize=(10, 6), dpi=150)
    ax = fig.add_subplot(111, projection='3d')
    fig.suptitle(f"{dev} Area Model Surface", fontsize=14, fontweight='bold', color=IHP_BLUE)
    
    ax.scatter(l_vals, nx_vals, area_vals, color=SCATTER_ORANGE, s=50, edgecolor='w', alpha=1.0, label="Measured Data")
    
    l_grid, nx_grid = np.meshgrid(np.linspace(l_vals.min(), l_vals.max(), 20),
                                  np.linspace(nx_vals.min(), nx_vals.max(), 20))
    area_grid = hbt_area(l_grid, nx_grid)
    
    ax.plot_surface(l_grid, nx_grid, area_grid, color=IHP_BLUE, alpha=0.5, edgecolor='none')
    ax.set_xlabel("l (µm)"); ax.set_ylabel("nx"); ax.set_zlabel("Area (µm²)")
    
    plt.tight_layout()
    plt.savefig(PLOTS_DIR / f"ihp130_{dev}.png")
    plt.close()

# ════════════════════════════════════════════════════════════════════════
# FIG 12: Residuals
# ════════════════════════════════════════════════════════════════════════
devs = []
max_errs = []
for cat in ["mosfets", "resistors", "mim_caps", "hbts"]:
    for dev, data in db.get(cat, {}).items():
        m = data["model"]
        pts = data["sweep"]
        if not pts: continue
        A = np.array([p["area_um2"] for p in pts])
        if cat == "mosfets":
            P = np.array([(m["ah"]*p["w"]+m["bh"])*(m["aw"]*p["l"]*p["nf"]+m["bw"]*p["nf"]+m["cw"]) for p in pts])
        elif cat == "resistors":
            P = np.array([next(me for me in m if np.isclose(me["w"], p["w"]))["slope"] * p["l"] + 
                          next(me for me in m if np.isclose(me["w"], p["w"]))["intercept"] for p in pts])
        elif cat == "mim_caps":
            P = np.array([(p["w"]+2*m["border_um"])*(p["l"]+2*m["border_um"]) for p in pts])
        elif cat == "hbts":
            if "fixed_h" in m: P = np.array([m["fixed_h"]*(m["aw"]*p["nx"]+m["bw"]) for p in pts])
            else: P = np.array([(m["ah"]*p["l"]+m["bh"])*(m["aw"]*p["nx"]+m["bw"]) for p in pts])
        err = np.abs((P - A) / A * 100).max()
        devs.append(dev)
        max_errs.append(err)

fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
colors = ['#27AE60' if e < 5 else '#F39C12' if e < 10 else '#C0392B' for e in max_errs]
y_pos = np.arange(len(devs))
ax.barh(y_pos, max_errs, color=colors, edgecolor='white')
ax.set_yticks(y_pos)
ax.set_yticklabels(devs)
ax.invert_yaxis()  
ax.set_xlabel("Maximum Point Error (%)")
ax.set_title("Maximum Prediction Error by Device", fontsize=14, fontweight='bold', color=IHP_BLUE)
for i, v in enumerate(max_errs):
    ax.text(v + 0.1, i, f"{v:.2f}%", va='center', fontweight='bold', color='#333333')
plt.tight_layout()
plt.savefig(PLOTS_DIR / "ihp130_residuals.png")
plt.close()

# ════════════════════════════════════════════════════════════════════════
# FIG 13: Summary Table
# ════════════════════════════════════════════════════════════════════════
fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
ax.axis('tight'); ax.axis('off')
table_data = [["Device", "Sweep Pts", "Max Error %", "Model Type"]]
for dev in devs:
    cat = next(c for c in ["mosfets", "resistors", "mim_caps", "hbts"] if dev in db.get(c, {}))
    pts = len(db[cat][dev]["sweep"])
    err = max_errs[devs.index(dev)]
    table_data.append([dev, str(pts), f"{err:.2f}%", cat[:-1].capitalize()])

table = ax.table(cellText=table_data, loc='center', cellLoc='center')
table.auto_set_font_size(False)
table.set_fontsize(12)
table.scale(1, 1.8)
for i in range(len(table_data[0])):
    table[(0, i)].set_facecolor(IHP_BLUE)
    table[(0, i)].set_text_props(color='white', weight='bold')
for i in range(1, len(table_data)):
    bg = '#EBF5FB' if i % 2 == 0 else 'white'
    for j in range(len(table_data[0])):
        table[(i, j)].set_facecolor(bg)

plt.title("Model Comparison Summary", fontsize=16, fontweight='bold', color=IHP_BLUE)
plt.tight_layout()
plt.savefig(PLOTS_DIR / "ihp130_model_comparison.png")
plt.close()

print("13 plots generated successfully.")
