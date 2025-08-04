# etsi/watchdog/drift_check.py
from typing import List, Dict
import pandas as pd
from .drift.factory import get_drift_function
from .config.schema import AlgorithmConfig

class DriftCheck:
    """
    DriftCheck — Core API to detect drift between reference and current datasets.
    This class can run multiple drift detection algorithms in a single pass.
    """

    def __init__(self, reference_df: pd.DataFrame):
        """
        Initializes the DriftCheck with the reference dataset.
        """
        self.reference_df = reference_df

    def run(self, current_df: pd.DataFrame, features: List[str], algorithms: List[AlgorithmConfig]) -> Dict:
        """
        Runs drift detection on a list of features using a list of algorithm configurations.

        Args:
            current_df (pd.DataFrame): The dataset to compare against the reference data.
            features (List[str]): A list of feature names to check for drift.
            algorithms (List[AlgorithmConfig]): A list of algorithm configurations from the config schema.

        Returns:
            Dict: A nested dictionary with drift results. 
                  Example: {'age': {'psi': <DriftResult>}, 'salary': {'psi': <DriftResult>}}
        """
        all_results = {}

        for feat in features:
            if feat not in self.reference_df.columns or feat not in current_df.columns:
                print(f"[etsi-watchdog] Skipping '{feat}' — missing in one of the datasets.")
                continue

            all_results[feat] = {}
            for algo_config in algorithms:
                try:
                    drift_function = get_drift_function(algo_config.name)
                    result = drift_function(
                        reference_df=self.reference_df,
                        current_df=current_df,
                        feature=feat,
                        threshold=algo_config.threshold
                    )
                    if result is not None:
                        all_results[feat][algo_config.name] = result

                except Exception as e:
                    print(f"[etsi-watchdog] Error running algorithm '{algo_config.name}' on feature '{feat}': {e}")
        
        return all_results