# test/test_v2.2.py

import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta
from etsi.watchdog import DriftCheck, Monitor, DriftComparator


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

    check = DriftCheck(ref)
    results = check.run(live, features=["age", "salary", "gender"])

    for feat, result in results.items():
        print(f"[✓] DriftCheck ({feat}) passed.")
        print(result.summary())
        result.plot()
        result.to_json(f"logs/drift_{feat}.json")


def test_monitor():
    print("\n==== Running DriftMonitor ====")
    ref, _ = generate_data()
    monitor = Monitor(ref)
    monitor.enable_logging("logs/rolling_log.csv")

    live = []
    for i in range(10):
        d = pd.DataFrame({
            'age': np.random.normal(30 + i, 5, 60),
            'salary': np.random.normal(50000 + i * 200, 10000, 60),
            'gender': np.random.choice(['M', 'F'], 60)
        })
        d.index = pd.date_range(start=datetime.today() - timedelta(days=9 - i), periods=60, freq="min")
        live.append(d)

    live_df = pd.concat(live)
    results = monitor.watch_rolling(live_df, window=50, freq="D", features=["age", "salary"])

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


if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    test_drift_check()
    test_monitor()
    test_comparator()
    print("\n---All watchdog component tests passed----")
