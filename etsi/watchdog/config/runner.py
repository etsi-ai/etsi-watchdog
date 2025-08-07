# etsi/watchdog/config/runner.py
import pandas as pd
import json
from pathlib import Path
from .schema import DriftConfig, OutputConfig


class ConfigRunner:
    """Runs a drift check based on a DriftConfig object."""

    def run(self, config: DriftConfig):
        """
        Executes the drift detection workflow from a DriftConfig object.
        """
        print("Loading data...")
        ref_data = pd.read_csv(config.reference_data)
        current_data = pd.read_csv(config.current_data)
        print(f"Loaded reference data: {len(ref_data)} rows")
        print(f"Loaded current data: {len(current_data)} rows")
        from ..drift_check import DriftCheck

        check = DriftCheck(ref_data)

        print(f"Running drift check on features: {config.features}")
        results = check.run(
            current_data, 
            features=config.features,
            algorithms=config.algorithms 
        )

        self._save_results(results, config.output)
        
        if config.output.visualizations:
             print("Generating visualizations...")
             for feature, result_group in results.items():
                 for algo_name, result in result_group.items():
                    if hasattr(result, 'plot'):
                        print(f"  Plotting {algo_name} for {feature}...")
                        result.plot()

        print("Drift check complete.")
        return results

    def _save_results(self, results, output_config: OutputConfig):
        """Save results based on output configuration."""
        output_path = Path(output_config.path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_config.format == "json":
            # This helper function needs to be able to handle the nested results
            json_results = self._results_to_dict(results)
            with open(output_path, "w") as f:
                json.dump(json_results, f, indent=2)
            print(f"Results saved to: {output_path}")

    def _results_to_dict(self, results):
        """
        Convert your potentially nested DriftCheck results to a serializable dictionary.
        """
        serializable_results = {}
        for feature, result_group in results.items():
            serializable_results[feature] = {}
            for algo_name, result in result_group.items():
                 serializable_results[feature][algo_name] = {
                    'drift_detected': getattr(result, 'drift_detected', False),
                    'value': getattr(result, 'value', None), # e.g., PSI or K-S statistic
                    'threshold': getattr(result, 'threshold', None),
                 }
        return serializable_results