# test/test_v2.2.py

import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta
from etsi.watchdog import DriftCheck, Monitor, DriftComparator
from etsi.watchdog.config import run_from_yaml


def generate_data():
    np.random.seed(42)
    ref = pd.DataFrame({
        'age': np.random.normal(30, 5, 500),
        'salary': np.random.normal(50000, 10000, 500),
        'gender': np.random.choice(['M', 'F'], 500)
    })

    live = pd.DataFrame({
        'age': np.random.normal(35, 5, 500),
        'salary': np.random.normal(55000, 15000, 500),
        'gender': np.random.choice(['M', 'F'], 500)
    })
    return ref, live


def test_drift_check():
    print("\n===== Running DriftCheck ====")
    ref, live = generate_data()

    # --- FIX 1: Removed 'ref_data=' keyword ---
    check = DriftCheck(ref)
    results = check.run(live, features=["age", "salary", "gender"])

    for feat, result in results.items():
        print(f"[✓] DriftCheck ({feat}) passed.")
        print(result.summary())


def test_monitor():
    print("\n==== Running Monitor ====")
    ref, _ = generate_data()
    monitor = Monitor(reference_df=ref)
    monitor.enable_logging("logs/rolling_log.csv")

    live = []
    for i in range(10):
        d = pd.DataFrame({
            'age': np.random.normal(30 + i, 5, 60),
            'salary': np.random.normal(50000 + i * 200, 10000, 60),
            'gender': np.random.choice(['M', 'F'], 60)
        })
        d.index = pd.to_datetime(d.index)
        live.append(d)

    live_df = pd.concat(live)
    results = monitor.watch_rolling(live_df, window=50, freq="D", features=["age", "salary"])

    if results:
        for date, res in results:
            print(f"{date.date()} —")
            for feat, result in res.items():
                print(f"  {feat}: {result.summary()}")


def test_comparator():
    print("\n ==== Running DriftComparator ====")
    ref, live1 = generate_data()
    _, live2 = generate_data()
    live2['age'] -= 2  # simulate correction

    check = DriftCheck(ref)
    r1 = check.run(live1, features=["age", "salary"])
    r2 = check.run(live2, features=["age", "salary"])

    comp = DriftComparator(r1, r2)
    diff = comp.diff()

    print("[✓] DriftComparator passed")
    for k, v in diff.items():
        print(f"{k}: Δ PSI = {v:+.4f}")


def test_config_runner():
    """Tests the YAML configuration system end-to-end."""
    print("\n==== Running ConfigRunner from YAML ====")

    config_content = """
drift_detection:
  reference_data: "logs/reference_data.csv"
  current_data: "logs/live_data.csv"
  features:
    - "age"
    - "salary"
  algorithms:
    - name: "psi"
      threshold: 0.2
  output:
    format: "json"
    path: "logs/config_drift_report.json"
    visualizations: false
"""
    config_path = "logs/test_config.yaml"
    with open(config_path, 'w') as f:
        f.write(config_content)

    ref, live = generate_data()
    ref.to_csv("logs/reference_data.csv", index=False)
    live.to_csv("logs/live_data.csv", index=False)

    run_from_yaml(config_path)
    print(f"[✓] ConfigRunner test passed. Check 'logs/config_drift_report.json'")


if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    test_drift_check()
    test_monitor()
    test_comparator()
    test_config_runner()
    print("\n---All watchdog component tests passed----")