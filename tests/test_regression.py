import json
import numpy as np

with open("device_db.json") as f:
    db = json.load(f)

for dev, data in db["mosfets"].items():
    pts = data.get("sweep", [])
    if len(pts) < 3: continue
    
    print(f"\n--- {dev} ---")
    
    # 1. Height model: h_um = a_h * w + c_h
    X_h = np.array([[p["w"], 1.] for p in pts])
    y_h = np.array([p["h_um"] for p in pts])
    c_h, *_ = np.linalg.lstsq(X_h, y_h, rcond=None)
    a_h, b_h = c_h
    
    print(f"Height model: h_um = {a_h:.4f} * W + {b_h:.4f}")
    
    # 2. Width model: w_um = a_w * (L*nf) + b_w * nf + c_w
    # Wait, does L appear by itself? 
    # nf fingers, each has L. 
    X_w = np.array([[p["l"]*p["nf"], p["nf"], 1.] for p in pts])
    y_w = np.array([p["w_um"] for p in pts])
    c_w, *_ = np.linalg.lstsq(X_w, y_w, rcond=None)
    a_w, b_w, const_w = c_w
    
    print(f"Width model: w_um = {a_w:.4f} * (L*nf) + {b_w:.4f} * nf + {const_w:.4f}")
    
    # Compare errors
    # Old model: Area = a*(w*nf) + b*l + c
    X_old = np.array([[p["w"]*p["nf"], p["l"], 1.] for p in pts])
    y_area = np.array([p["area_um2"] for p in pts])
    c_old, *_ = np.linalg.lstsq(X_old, y_area, rcond=None)
    
    for p in pts:
        # Predict old
        pred_old = c_old[0]*(p["w"]*p["nf"]) + c_old[1]*p["l"] + c_old[2]
        err_old = abs(pred_old - p["area_um2"]) / p["area_um2"] * 100
        
        # Predict new
        pred_h = a_h * p["w"] + b_h
        pred_w = a_w * (p["l"]*p["nf"]) + b_w * p["nf"] + const_w
        pred_new = pred_h * pred_w
        err_new = abs(pred_new - p["area_um2"]) / p["area_um2"] * 100
        
        print(f"W={p['w']} L={p['l']} nf={p['nf']} | Area={p['area_um2']:.2f} | Old={pred_old:.2f} ({err_old:.1f}%) | New={pred_new:.2f} ({err_new:.1f}%)")
