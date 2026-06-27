# SKY130 Area Approximator

This repository contains robust, highly accurate Python scripts designed for estimating the physical layout area of analog/mixed-signal blocks in the **Skywater 130nm** (SKY130) node directly from SPICE netlists—without actually drawing the layout!

## 🚀 Key Features

1. **Physical Accuracy**: Instead of assuming area scales perfectly linearly, our mathematical model calculates Bounding-Box area as a product of bounding-box height (`h_um`) and width (`w_um`). It fits `Height` as a function of drawn `Width`, and `Width` as a function of `Length * nf`. This successfully models the non-linear cross terms, guaranteeing <2.5% estimation error even for extreme analog W/L ratios (e.g. `W=40, L=5`).
2. **Automated Magic DB Generation**: Comes with an automated magic-invoking script (`sky130_measure_devices.py`) that sweeps 750+ unique device topologies (W/L/nf) and logs their bounding boxes into a JSON database.
3. **Smart Netlist Parsing**: The `sky130_area_estimator.py` parses `xschem`-generated `.spice` netlists, maps instances to the database, applies a routing overhead multiplier, and sums up the total area.
4. **Area Budget Tracking**: Integrated `--budget` flag to help analog designers verify if their block fits in the allotted floorplan!

## 🛠 Usage

### 1. Estimating Area
Run the estimator script on your design SPICE netlist:

```bash
python3 sky130_area_estimator.py --netlist /path/to/my_design.spice --budget 16000
```
**Output Example:**
```text
  ────────────────────────────────────────────────────────────────────────
  Devices subtotal                                                12319.84 µm²
  Routing overhead (×1.3)                                          3695.95 µm²
  ────────────────────────────────────────────────────────────────────────
  TOTAL ESTIMATED AREA                                            16015.79 µm²
  Equivalent square side                                            126.55 µm
  Area Budget Allowed                                             16000.00 µm²
  Area Left Over                                                    -15.79 µm²
  Utilization                                                        100.1 %
  ❌ WARNING: You are OVER BUDGET by 15.79 µm²!
```

### 2. Regenerating the Database
If you wish to add custom device sizes to the database or are using a different process node variant, you can re-run the magic sweep script.
It will automatically search for your PDK using `$PDK_ROOT`, or you can supply custom paths:

```bash
python3 sky130_measure_devices.py --mag-path /path/to/pdk/sky130A/libs.ref/sky130_fd_pr/mag
```
This script will:
- Silently open magic in `-dnull` mode.
- Instantiate hundreds of MOSFETs, Resistors, and Capacitors.
- Patch the `sky130::ruleset` TCL bug natively.
- Measure physical bounding boxes.
- Run advanced linear regressions using `numpy` to map parameters to formulas.
- Output a completed `device_db.json`.

## 📁 Repository Structure
- `sky130_measure_devices.py`: Automated layout extraction & model fitting.
- `device_db.json`: The fitted parameters and raw bounding box dataset.
- `sky130_area_estimator.py`: SPICE parser and area calculator.
- `Comprehensive_Model_Report.docx`: Detailed document showing mathematical parity plots and error margins.
