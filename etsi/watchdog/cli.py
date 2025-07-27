# etsi/watchdog/cli.py

import argparse
import pandas as pd
from . import DriftCheck
from .report.generate import generate_drift_report  


def run_cli():
    parser = argparse.ArgumentParser(description="ETSI Drift Watchdog CLI")

    parser.add_argument("--ref", required=True, help="Path to reference CSV file")
    parser.add_argument("--live", required=True, help="Path to live CSV file")
    parser.add_argument("--features", nargs="+", help="List of features to check")
    parser.add_argument("--algo", default="psi", help="Drift algorithm (default: psi)")
    parser.add_argument("--threshold", type=float, default=0.2, help="Drift threshold (default: 0.2)")
    parser.add_argument("--out", help="Base output filename (used for JSON or report)")
    
    # New optional flag for report generation
    parser.add_argument("--report", choices=["pdf", "html"], help="Generate a visual drift report (pdf or html)")

    args = parser.parse_args()

    ref_df = pd.read_csv(args.ref)
    live_df = pd.read_csv(args.live)

    checker = DriftCheck(ref_df, algorithm=args.algo, threshold=args.threshold)
    results = checker.run(live_df, features=args.features or list(ref_df.columns))

    if args.report:
        # Generate the full visual report
        out_path = f"{args.out or 'drift_report'}.{args.report}"
        generate_drift_report(ref_df, live_df, results, path=out_path, format=args.report)
        print(f"\n✅ Drift report generated at: {out_path}")
    else:
        # Default: print or export JSON
        for feat, res in results.items():
            print(f"\n{feat} ➤ {res.summary()}")
            if args.out:
                res.to_json(f"{args.out}_{feat}.json")


if __name__ == "__main__":
    run_cli()
