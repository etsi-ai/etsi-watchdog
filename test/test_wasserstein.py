# test/test_wasserstein.py

import pandas as pd
import numpy as np
import os
from etsi.watchdog import DriftCheck

def generate_data():
    np.random.seed(42)
    ref = pd.DataFrame({
        'age': np.random.normal(30, 5, 500),
        'salary': np.random.normal(50000, 10000, 500)
    })

    live = pd.DataFrame({
        'age': np.random.normal(32, 5, 500),
        'salary': np.random.normal(52000, 10000, 500)
    })
    return ref, live

def test_wasserstein_drift_check():
    print("\n===== Running Wasserstein DriftCheck ====")
    ref, live = generate_data()

    check = DriftCheck(ref, algorithm="wasserstein", threshold=0.1)
    results = check.run(live, features=["age", "salary"])

    for feat, result in results.items():
        print(f"[✓] Wasserstein DriftCheck ({feat}) passed.")
        print(result.summary())
        
        # To save the results to a log file
        result.to_json(f"logs/wasserstein_drift_{feat}.json")

    print("\n---Wasserstein drift test passed----")

if __name__ == "__main__":
    if not os.path.exists("logs"):
        os.makedirs("logs")
    test_wasserstein_drift_check()