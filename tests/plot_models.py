import json
import numpy as np
import matplotlib.pyplot as plt

# Load DB
with open("device_db.json") as f:
    db = json.load(f)

# Devices to plot
devs_to_plot = ["nfet_01v8", "pfet_g5v0d10v5"]

fig, axes = plt.subplots(1, 2, figsize=(14, 6))

for i, dev in enumerate(devs_to_plot):
    data = db["mosfets"][dev]
    pts = data.get("sweep", [])
    
    # 1. Calculate old model parameters by re-running the flawed linear fit
    X_old = np.array([[p["w"]*p["nf"], p["l"], 1.] for p in pts])
    y_area = np.array([p["area_um2"] for p in pts])
    c_old, *_ = np.linalg.lstsq(X_old, y_area, rcond=None)
    
    # 2. Extract new model parameters
    m = data["model"]
    ah, bh, aw, bw, cw = m["ah"], m["bh"], m["aw"], m["bw"], m["cw"]
    
    # Prepare data for scatter
    actual_areas = []
    old_preds = []
    new_preds = []
    labels = []
    
    for p in pts:
        actual = p["area_um2"]
        # Old prediction
        pred_old = c_old[0]*(p["w"]*p["nf"]) + c_old[1]*p["l"] + c_old[2]
        # New prediction
        pred_h = ah * p["w"] + bh
        pred_w = aw * (p["l"]*p["nf"]) + bw * p["nf"] + cw
        pred_new = pred_h * pred_w
        
        actual_areas.append(actual)
        old_preds.append(pred_old)
        new_preds.append(pred_new)
        labels.append(f"W={p['w']}\nL={p['l']}\nnf={p['nf']}")

    ax = axes[i]
    
    # Parity plot
    min_val = min(min(actual_areas), min(old_preds), min(new_preds)) * 0.9
    max_val = max(max(actual_areas), max(old_preds), max(new_preds)) * 1.1
    
    # Perfect fit line
    ax.plot([min_val, max_val], [min_val, max_val], 'k--', alpha=0.5, label='Perfect Fit (Actual)')
    
    # Scatter points
    ax.scatter(actual_areas, old_preds, color='red', marker='x', s=80, label='Old Linear Model')
    ax.scatter(actual_areas, new_preds, color='blue', marker='o', s=50, alpha=0.7, label='New Bounding-Box Model')
    
    # Annotate some points
    for j, txt in enumerate(labels):
        if j % 2 == 0 or pts[j]['l'] == 0.5: # highlight long/wide devices
            ax.annotate(txt, (actual_areas[j], old_preds[j]), xytext=(5, 5), textcoords='offset points', fontsize=8, color='red', alpha=0.7)
            
    ax.set_title(f"{dev}: Model Predictions vs Actual Area")
    ax.set_xlabel("Actual Magic Measured Area (µm²)")
    ax.set_ylabel("Model Predicted Area (µm²)")
    ax.legend()
    ax.grid(True, linestyle=':', alpha=0.6)

plt.tight_layout()
plt.savefig("/home/nithin/.gemini/antigravity-cli/brain/157240fd-e723-4e23-8d03-f889c578db39/model_comparison.png", dpi=150)
print("Plot saved.")
