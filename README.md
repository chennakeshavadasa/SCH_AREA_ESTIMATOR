# Schematic Area Estimator (SKY130 + GF180MCU)
Estimates the pre-layout physical area of an analog/mixed-signal design directly from a SPICE netlist. Supports SKY130 and GF180MCU (gf180mcuC). 

## Quickstart
```bash
# SKY130
python3 sky130_area_estimator.py --netlist design.spice
# GF180MCU
python3 gf180mcu_area_estimator.py --netlist design.spice
```
Each script auto-loads its own `device_db.json`; use `--db` to override. Use the estimator matching the PDK your netlist was generated in (model prefixes must match: `sky130_fd_pr__` vs `gf180mcu_fd_pr__`).

## Budgeting (optional)
```bash
python3 gf180mcu_area_estimator.py --netlist design.spice --budget "700um x 700um"
```
Prints utilization % and an over-budget warning.

## Models
- **MOSFET:**

$$Area = (a_h W + b_h)\,(a_w L n_f + b_w n_f + c_w)$$

- **Resistor:**

$$Area = \text{slope}\cdot L + \text{intercept}$$

- **MIM capacitor:**

$$Area = (W + 2b)(L + 2b)$$

- **BJT:** fixed macro lookup (area read directly from the device table)

## For more accurate results
The estimator does **not** measure your layout, it reads pre-computed model coefficients from `device_db.json` and plugs them into the formulas above. The GF180MCU database currently ships with **placeholder coefficients** (structurally valid but not real GF180MCU geometry), so its numbers are meaningless until rebuilt.

To generate real values, run the measurement backend against an installed PDK (requires **Magic** on your `PATH`). In each command below, `cd scripts` enters the backend folder, `PDK_ROOT=/path/to/pdk` tells the script where the PDK lives, and the Python script drives Magic to measure every device's bounding box, regress the coefficients, and overwrite `device_db.json`. Run only the line matching the PDK you're estimating for:
```bash
# SKY130
cd scripts && PDK_ROOT=/path/to/pdk python3 sky130_measure_devices.py
# GF180MCU
cd scripts && PDK_ROOT=/path/to/pdk python3 gf180mcu_measure_devices.py
```

## PDK variant / location
The GF180MCU backend expects the **gf180mcuC** variant under `$PDK_ROOT/gf180mcuC/...`. To use a different metal stack (gf180mcuA/B/D), edit the `gf180mcuC` path near the top of `scripts/gf180mcu_measure_devices.py` (`find_mag`). Device geometries are identical across variants, so the DB is reusable (only the MIM / top-metal cap layers differ).
