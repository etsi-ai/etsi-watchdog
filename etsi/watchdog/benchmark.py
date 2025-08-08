"""
Benchmarking Suite for Drift Metrics (v2.2)

This module generates synthetic datasets with different drift scenarios and benchmarks available drift metrics (KS, PSI).
"""
import time
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import argparse
from etsi.watchdog.drift.ks import ks_drift
from etsi.watchdog.drift.psi import psi_drift

DRIFT_SCENARIOS = [
    {
        "name": "mean_shift",
        "generator": lambda n: (np.random.normal(0, 1, n), np.random.normal(1, 1, n)),
    },
    {
        "name": "variance_change",
        "generator": lambda n: (np.random.normal(0, 1, n), np.random.normal(0, 2, n)),
    },
    {
        "name": "multimodal",
        "generator": lambda n: (np.random.normal(0, 1, n), np.concatenate([np.random.normal(-2, 1, n//2), np.random.normal(2, 1, n//2)])),
    },
]

METRICS = [
    {
        "name": "KS",
        "func": ks_drift,
    },
    {
        "name": "PSI",
        "func": psi_drift,
    },
]

def benchmark_metrics(n_samples=1000):
    results = []
    for scenario in DRIFT_SCENARIOS:
        ref, cur = scenario["generator"](n_samples)
        reference_df = pd.DataFrame({"feature": ref})
        current_df = pd.DataFrame({"feature": cur})
        for metric in METRICS:
            start = time.time()
            drift_result = metric["func"](reference_df, current_df, "feature")
            elapsed = time.time() - start
            if drift_result is not None:
                results.append({
                    "scenario": scenario["name"],
                    "metric": metric["name"],
                    "score": drift_result.score,
                    "threshold": drift_result.threshold,
                    "sample_size": drift_result.sample_size,
                    "runtime": elapsed,
                    "details": drift_result.details,
                })
    return results


def print_results(results, plot=False, save_path=None):
    df = pd.DataFrame(results)
    print(df[["scenario", "metric", "score", "threshold", "sample_size", "runtime"]])
    if plot:
        fig, ax = plt.subplots(figsize=(8, 5))
        for metric in df['metric'].unique():
            subset = df[df['metric'] == metric]
            ax.plot(subset['scenario'], subset['score'], marker='o', label=metric)
        ax.set_xlabel('Drift Scenario')
        ax.set_ylabel('Score')
        ax.set_title('Drift Metric Scores by Scenario')
        ax.legend()
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path)
            print(f"Plot saved to {save_path}")
        else:
            plt.show()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark drift metrics (KS, PSI) on synthetic drift scenarios.")
    parser.add_argument('--samples', type=int, default=1000, help='Number of samples per scenario')
    parser.add_argument('--plot', action='store_true', help='Show plot of metric scores')
    parser.add_argument('--save', type=str, default=None, help='Path to save plot image')
    args = parser.parse_args()

    results = benchmark_metrics(n_samples=args.samples)
    print_results(results, plot=args.plot, save_path=args.save)
