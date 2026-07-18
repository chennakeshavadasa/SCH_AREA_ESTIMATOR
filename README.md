# Schematic Area Estimator (SKY130 + GF180MCU + IHP130)

This tool provides analog and mixed-signal designers with a highly accurate method for estimating the physical layout area of a design directly from a SPICE netlist. It supports the **Skywater 130nm (SKY130)**, **GlobalFoundries 180nm (GF180MCU)**, and **IHP 130nm SG13G2 (IHP130)** process design kits. It eliminates the need for premature layout generation when determining area budgets and floorplanning constraints.

---

## Repository Structure

The files within this repository are organized as follows:

- **sky130/sky130_area_estimator.py**: The primary execution script for SKY130. It parses a target SPICE netlist, applies the mathematical area models, and outputs the total estimated area.
- **gf180/gf180mcu_area_estimator.py**: The equivalent execution script for GF180MCU.
- **ihp130/ihp130_area_estimator.py**: The equivalent execution script for IHP130 SG13G2.
- **sky130/device_db.json**: The pre-calculated database for SKY130. Contains all regression parameters and physical bounding-box dimensions required for SKY130 devices.
- **gf180/device_db.json**: The pre-calculated database for GF180MCU. Currently ships with placeholder coefficients — rebuild with the measurement backend for real values (see [Advanced Usage](#advanced-usage-rebuilding-the-database) below).
- **ihp130/device_db.json**: The pre-calculated database for IHP130 SG13G2. Contains fully measured coefficients for all devices (LV/HV MOSFETs, polysilicon resistors, MIM caps, and npn13g2/npn13g2l/npn13g2v HBTs).
- **reports/**: Contains detailed reports (in both `.docx` and `.pdf` formats) documenting the mathematical models used and providing parity plots demonstrating their accuracy against actual physical layouts.
- **sky130/scripts/**, **gf180/scripts/**, **ihp130/scripts/**: Backend tools that interface with the Magic VLSI layout tool. Each `<pdk>_measure_devices.py` measures physical device bounding boxes and performs regressions to generate the respective `device_db.json` files.
- **ihp130/tests/**: Verification and sanity-check scripts for the IHP130 backend (`validate_db_and_estimator.py`, `verify_coefficients.py`, `ihp130_test_one_device.py`).
- **tests/**: Contains legacy verification, plotting, and diagnostic scripts used during the development phase of the estimator.

---

## Quickstart Guide for Beginners

If you are new to the tool and simply wish to estimate the area of your design, follow these steps:

1. **Open a terminal** and navigate to the directory where you cloned this repository.

```
cd /path/to/SCH_AREA_ESTIMATOR
```

2. **Execute the estimator script** for your PDK, providing the path to your target SPICE netlist.

```
# SKY130
python3 sky130/sky130_area_estimator.py --netlist /path/to/your_design.spice

# GF180MCU
python3 gf180/gf180mcu_area_estimator.py --netlist /path/to/your_design.spice

# IHP130 SG13G2
python3 ihp130/ihp130_area_estimator.py --netlist /path/to/your_design.spice
```

> **Note:** Use the estimator matching the PDK your netlist was generated in. Model name prefixes must match: `sky130_fd_pr__` for SKY130, `gf180mcu_fd_pr__` for GF180MCU, and `sg13g2::` / `sg13_` for IHP130.

3. **Review the output.** The script will print the physical area of the devices, the routing overhead, and the total estimated square footprint directly to the terminal.

Each script auto-loads its own `device_db.json`. Use the `--db` flag to override with a custom database path.

---

## Detailed Usage Guide

### 1. Standard Area Estimation

To calculate the estimated layout area for a specific design, execute the estimator script and provide the path to your SPICE netlist.

```
python3 sky130_area_estimator.py --netlist /path/to/your_design.spice
```

### 2. Area Estimation with Budgeting

If a specific area budget has been assigned to your block, you can provide it to the script using the `--budget` flag. You can provide the budget as a total area (e.g., `16000`) or as X and Y dimensions (e.g., `"100x150"`, `"130um x 130um"`, or `"100 vs 150"`). The script will parse the dimensions, calculate the utilization percentage, and indicate the remaining available area.

```
python3 sky130_area_estimator.py --netlist /path/to/your_design.spice --budget "130um x 130um"
```

**Example Output:**

```
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

- **Height (Y-axis):** Determined by the drawn width (`W`). Modeled as `Height = a·W + b`.
- **Width (X-axis):** Determined by the drawn length (`L`) and number of fingers (`nf`). Modeled as `Width = c·(L·nf) + d·nf + e`.
- **Area:** Calculated as the product of the independent dimensions.

$$\text{Area} = (a_h W + b_h)(a_w L n_f + b_w n_f + c_w)$$

This guarantees estimation error stays below 2.5% even for extreme transistor sizes like `W=10, L=10, nf=10`.

### 2. Resistor Area Model

Polysilicon and diffusion resistors are modeled via linear regression across their drawn length, specific to their drawn width.

$$\text{Area} = \text{slope} \cdot L + \text{intercept}$$

where Slope and Intercept are independently fit for every valid drawn Width in the PDK.

### 3. Capacitor Area Model

MIM and Varactor capacitors are modeled based on a fixed layout boundary overhead.

$$\text{Area} = (W + 2b)(L + 2b)$$

### 4. BJT Model

Bipolar Junction Transistors (BJTs) are provided in SKY130 as fixed-size macros. The database maps the discrete sizes to their exact static footprint.

---

## Developer Verification & Testing

If you want to verify that the raw Magic TCL extraction logic is functioning correctly on your machine *before* running the massive 700+ point sweep or blindly trusting the area models, you can run the isolated test scripts located in the `tests/` directory.

These scripts were developed exactly for that purpose — to overcome initial pathing and TCL extraction hurdles:

- **`tests/test_one_device.py`**: A minimal sanity check that connects to Magic, instantiates a single `nfet_01v8`, parses its bounding box, and prints it out. Run this first to verify your `$PDK_ROOT` and Magic paths are sound.
- **`tests/comprehensive_test.py`**: An older, highly-verbose script used to test the initial linear regression and debug Magic DRC loading issues.

**To run the basic sanity check:**

```
cd tests
python3 test_one_device.py
```

---

## Advanced Usage: Rebuilding the Database

If you need to support custom device sizes outside the bounds of the existing database, or if you are adapting this tool for a modified PDK variant, you can regenerate the database locally.

This requires the Magic VLSI layout tool to be installed on your system. The `PDK_ROOT` environment variable must point to your installed PDK.

```
# SKY130
cd sky130/scripts && PDK_ROOT=/path/to/pdk python3 sky130_measure_devices.py

# GF180MCU
cd gf180/scripts && PDK_ROOT=/path/to/pdk python3 gf180mcu_measure_devices.py

# IHP130 SG13G2
cd ihp130/scripts && PDK_ROOT=/path/to/pdk python3 ihp130_measure_devices.py
```

The script will automatically detect your PDK location using the `PDK_ROOT` environment variable. It operates Magic in batch mode (`-dnull`) to instantiate devices, extract exact physical bounding boxes, perform linear regression on the extracted dimensions, and update `device_db.json`.

> **Note on GF180MCU placeholder coefficients:** The GF180MCU database currently ships with structurally valid but unverified placeholder coefficients. Its area estimates will be inaccurate until you run the measurement backend above against a real GF180MCU PDK installation.

---

## PDK Variant Notes (GF180MCU)

The GF180MCU backend expects the **gf180mcuC** variant under `$PDK_ROOT/gf180mcuC/...`. To use a different metal stack (gf180mcuA/B/D), edit the `gf180mcuC` path near the top of `scripts/gf180mcu_measure_devices.py`. Device geometries are identical across variants, so the database is reusable — only the MIM / top-metal capacitor layers differ.
