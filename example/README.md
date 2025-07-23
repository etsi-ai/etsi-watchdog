<!-- ANY OTHER SUGGESTIONS THAT ARE TO BE MADE CAN ALSO BE MADE HERE BY OPEN SOURCE BEGINNERS. PROJECT ADMINS CAN CHECK YOUR SUGGESTIONS FROM HERE -->

# Drift Detection on Tabular Data using Watchdog

Contributed by: Disha Oza**

This example script demonstrates how to use the `DriftMonitor` class from the `etsi.watchdog` package to detect **data drift** using the **Population Stability Index (PSI)**.

---

## What This Script Does

- Loads **reference** and **live** datasets using pandas.
- Detects **significant changes in data distribution** between the two datasets.
- Classifies drift levels based on PSI thresholds:
  - **PSI < 0.1** → No Drift
  - **PSI 0.1 to 0.2** → Moderate Drift
  - **PSI > 0.2** → Significant Drift
- Prints PSI scores and drift classifications in the terminal.
- Saves PSI scores and drift types to a CSV file: `drift_report.csv` (for easy reference and visualization).
- Added **clear inline comments** in `test_watchdog.py` to help beginners understand each step.

---

## File Location

- Modified file: `test/test_watchdog.py`
- This README: `example/README.md`

---

## Suggested Improvements (Optional for Maintainers)

- Add visualizations (e.g., histograms or bar plots) to show drift changes graphically.

- Consider automated alerts if drift exceeds a threshold.
