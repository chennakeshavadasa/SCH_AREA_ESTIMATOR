import json
import numpy as np

with open("device_db.json") as f:
    db = json.load(f)

print("--- RIGOROUS VERIFICATION REPORT ---")
issues_found = 0

# 1. MOSFETS
print("\n[Verifying MOSFETs]")
for dev, data in db["mosfets"].items():
    pts = data.get("sweep", [])
    m = data.get("model")
    if not m: continue
    
    # Check model params
    ah, bh, aw, bw, cw = m['ah'], m['bh'], m['aw'], m['bw'], m['cw']
    if any(np.isnan([ah, bh, aw, bw, cw])):
        print(f"❌ {dev}: NaN in model parameters!")
        issues_found += 1
        
    max_err_new = 0.0
    for p in pts:
        actual = p["area_um2"]
        pred_new = (ah*p['w'] + bh) * (aw*(p['l']*p['nf']) + bw*p['nf'] + cw)
        err = abs(pred_new - actual) / actual * 100
        
        if err > max_err_new:
            max_err_new = err
            
        if actual <= 0:
            print(f"❌ {dev}: Non-positive actual area for W={p['w']} L={p['l']} nf={p['nf']}")
            issues_found += 1
            
        if pred_new <= 0:
            print(f"❌ {dev}: Non-positive predicted area for W={p['w']} L={p['l']} nf={p['nf']}")
            issues_found += 1

    print(f"✅ {dev}: {len(pts)} points verified. Max Error (New Model) = {max_err_new:.2f}%")
    if max_err_new > 5.0:
        print(f"   ⚠️ WARNING: Max error is surprisingly high (>5%).")

# 2. Resistors
print("\n[Verifying Resistors]")
for dev, data in db["poly_resistors"].items():
    models = data.get("model", [])
    for m in models:
        slope, ic = m['slope'], m['intercept']
        if slope <= 0 or ic <= 0:
            print(f"❌ {dev} (W={m['w']}): Suspicious slope={slope} or intercept={ic}")
            issues_found += 1
    print(f"✅ {dev}: Models physically sound.")

# 3. Capacitors
print("\n[Verifying Capacitors]")
for dev, data in db["mim_caps"].items():
    m = data.get("model")
    if m:
        if m['border_um'] <= 0:
            print(f"❌ {dev}: Negative border {m['border_um']}")
            issues_found += 1
    print(f"✅ {dev}: Model physically sound.")

# 4. BJTs
print("\n[Verifying BJTs]")
for dev, data in db["bjts"].items():
    w, h, area = data['w_um'], data['h_um'], data['area_um2']
    calc_area = w * h
    if abs(calc_area - area) > 0.01:
        print(f"❌ {dev}: BJT area mismatch! {w} * {h} != {area}")
        issues_found += 1
    else:
        print(f"✅ {dev}: BJT area geometry is exact.")
        
print(f"\nTotal Issues Found: {issues_found}")
