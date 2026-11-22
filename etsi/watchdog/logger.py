import json
import os
from datetime import datetime


def log_drift(result, path, feature=None, extra=None):
    timestamp = datetime.now().isoformat()

    row = {
        "timestamp": timestamp,
        "feature": feature if feature else "unknown",
        "method": result.method,
        "score": result.score,
        "threshold": result.threshold,
        "drift": result.is_drifted,
        "sample_size": result.sample_size
    }

    if extra and isinstance(extra, dict):
        for k, v in extra.items():
            if k not in row:
                row[k] = v

    # Append to CSV or JSON based on extension
    log_dir = os.path.dirname(path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    if path.endswith(".json"):
        if os.path.exists(path):
            with open(path, "r") as f:
                logs = json.load(f)
        else:
            logs = []

        logs.append(row)
        with open(path, "w") as f:
            json.dump(logs, f, indent=2)

    elif path.endswith(".csv"):
        import pandas as pd
        df = pd.DataFrame([row])
        if not os.path.exists(path):
            df.to_csv(path, index=False)
        else:
            try:
                existing_cols = pd.read_csv(path, nrows=0).columns.tolist()
                new_cols = [c for c in df.columns if c not in existing_cols]
                if new_cols:
                    full_df = pd.read_csv(path)
                    combined = pd.concat([full_df, df], ignore_index=True)
                    combined.to_csv(path, index=False)
                else:
                    aligned_df = df.reindex(columns=existing_cols)
                    aligned_df.to_csv(path, mode="a", header=False, index=False)
            except Exception:
                df.to_csv(path, mode="a", header=False, index=False)

    print(f"[watchdog] Drift-Result logged for '{row['feature']}' → {path}")
