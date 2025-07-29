import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import pandas as pd
import numpy as np
from etsi.watchdog.drift.factory import get_drift_function
import json
import os

def test_tree_drift():
    print("\n🌲 ==== Running TreeDrift Test ====")
    ref = pd.DataFrame({
        "age": np.random.normal(30, 5, 300),
        "salary": np.random.normal(50000, 10000, 300)
    })
    tgt = pd.DataFrame({
        "age": np.random.normal(35, 5, 300),
        "salary": np.random.normal(55000, 12000, 300)
    })

    drift = get_drift_function("tree")()
    auc = drift.fit(ref, tgt)
    report = drift.get_drift_report()

    print(f"\n[✓] AUC Score: {auc:.4f}\n")
    print("📊 Drift Report:")
    print(json.dumps(report, indent=4))

    
    os.makedirs("logs", exist_ok=True)
    with open("logs/tree_drift_report.json", "w") as f:
        json.dump(report, f, indent=4)
    print("\n📝 Report saved to logs/tree_drift_report.json")

if __name__ == "__main__":
    test_tree_drift()
