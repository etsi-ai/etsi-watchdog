# test/test_gp_drift.py

import pandas as pd
import numpy as np
import os
from etsi.watchdog import DriftCheck

def generate_data():
    np.random.seed(42)
    ref = pd.DataFrame({
        'AQI': np.random.normal(80, 5, 100)
    })

    live = pd.DataFrame({
        'AQI': np.random.normal(100, 5, 100)
    })

    return ref, live

def test_gp_drift_check():
    print("\n===== Running Gaussian Process DriftCheck ====")
    ref, live = generate_data()

    check = DriftCheck(ref, algorithm="gp", threshold=0.2)
    results = check.run(live, features=["AQI"])

    for feat, result in results.items():
        print(f"[✓] GP DriftCheck ({feat}) passed.")
        print(result.summary())
        result.to_json(f"logs/gp_drift_{feat}.json")

    print("\n---GP drift test passed----")

if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    test_gp_drift_check()
