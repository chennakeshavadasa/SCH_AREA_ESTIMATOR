# SKY130 Schematic Area Estimator

This tool provides analog and mixed-signal designers with a highly accurate method for estimating the physical layout area of a Skywater 130nm (SKY130) design directly from a SPICE netlist. It eliminates the need for premature layout generation when determining area budgets and floorplanning constraints.

---

## Repository Structure

The files within this repository are organized as follows:

- **sky130_area_estimator.py**: The primary execution script. It parses a target SPICE netlist, applies the mathematical area models, and outputs the total estimated area.
- **device_db.json**: The pre-calculated database. This contains all regression parameters and physical bounding-box dimensions required for SKY130 devices.
- **reports/**: Contains detailed reports (in both `.docx` and `.pdf` formats) that document the mathematical models used and provide parity plots demonstrating their accuracy against actual physical layouts.
- **scripts/**: Contains `sky130_measure_devices.py`, a backend tool used to interface with the Magic VLSI layout tool. This script measures physical device bounding boxes and performs regressions to generate `device_db.json`.
- **tests/**: Contains legacy verification, plotting, and diagnostic scripts used during the development phase of the estimator.

---

## Quickstart Guide for Beginners

If you are new to the tool and simply wish to estimate the area of your design, follow these steps:

1. **Open a terminal** and navigate to the directory where you cloned this repository.
   ```bash
   cd /path/to/SCH_AREA_ESTIMATOR
   ```
2. **Execute the estimator script**, providing the path to your target SPICE netlist.
   ```bash
   python3 sky130_area_estimator.py --netlist /path/to/your_design.spice
   ```
3. **Review the output**. The script will print the physical area of the devices, the routing overhead, and the total estimated square footprint directly to the terminal.

---

## Detailed Usage Guide

### 1. Standard Area Estimation

To calculate the estimated layout area for a specific design, execute the estimator script and provide the path to your SPICE netlist.

```bash
python3 sky130_area_estimator.py --netlist /path/to/your_design.spice
```

### 2. Area Estimation with Budgeting

If a specific area budget has been assigned to your block, you can provide it to the script using the `--budget` flag. You can provide the budget as a total area (e.g., `16000`) or as X and Y dimensions (e.g., `"100x150"`, `"130um x 130um"`, or `"100 vs 150"`). The script will parse the dimensions, calculate the utilization percentage, and indicate the remaining available area.

```bash
python3 sky130_area_estimator.py --netlist /path/to/your_design.spice --budget "130um x 130um"
```

**Example Output:**
```text
  ────────────────────────────────────────────────────────────────────────
  Devices subtotal                                                12319.84 µm²
  Routing overhead (×1.3)                                          3695.95 µm²
  ────────────────────────────────────────────────────────────────────────
  TOTAL ESTIMATED AREA                                            16015.79 µm²
  Equivalent square side                                            126.55 µm
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Parsed 205 instances, costed 205, skipped 0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Area Budget Allowed                                             16000.00 µm²
  Area Left Over                                                    -15.79 µm²
  Utilization                                                        100.1 %
  WARNING: You are OVER BUDGET by 15.79 µm²!
```

---

## Mathematical Modeling Methodology

The core strength of this estimator is that it avoids simplistic linear assumptions (which fail heavily on non-linear analog sizing) in favor of decoupled bounding-box dimension modeling.

### 1. MOSFET Area Model
Instead of modeling Area directly, the script models the physical **Height** and physical **Width** independently:
- **Height (Y-axis):** Determined by the drawn width (`W`). Modeled as `Height = a * W + b`.
- **Width (X-axis):** Determined by the drawn length (`L`) and number of fingers (`nf`). Modeled as `Width = c * (L * nf) + d * nf + e`.
- **Area:** Calculated as the product of the independent dimensions: `Area = Height * Width`. 

This guarantees estimation error stays below 2.5% even for extreme transistor sizes like `W=10, L=10, nf=10`.

### 2. Resistor Area Model
Polysilicon and diffusion resistors are modeled via linear regression across their drawn length, specific to their drawn width. 
- **Area:** `Area = Slope * L + Intercept` (where Slope and Intercept are independently fit for every valid drawn Width in the PDK).

### 3. Capacitor Area Model
MIM and Varactor capacitors are modeled based on a fixed layout boundary overhead.
- **Area:** `Area = (W + 2 * border) * (L + 2 * border)`

### 4. BJT Model
Bipolar Junction Transistors (BJTs) are provided in SKY130 as fixed-size macros. The database simply maps the discrete sizes to their exact static footprint.

---

## Advanced Usage: Rebuilding the Database

If you need to support custom device sizes outside the bounds of the existing database, or if you are adapting this tool for a modified PDK variant, you can regenerate the database locally. 

This requires the Magic VLSI layout tool to be installed on your system.

```bash
cd scripts
python3 sky130_measure_devices.py
```

The script will automatically detect your PDK location using the `PDK_ROOT` environment variable. It operates Magic in batch mode (`-dnull`) to instantiate devices, extract exact physical bounding boxes, perform linear regression on the extracted dimensions, and update `device_db.json`.
