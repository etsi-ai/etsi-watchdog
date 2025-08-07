# etsi/watchdog/config/runner.py
import pandas as pd
import json
from pathlib import Path
from .parser import ConfigParser
from .schema import DriftConfig
from ..drift_check import DriftCheck  # Import your existing class

class ConfigRunner:
    def __init__(self):
        self.parser = ConfigParser()
    
    def run_from_config(self, config_path: str):
        """Run drift detection from YAML config."""
        config = self.parser.load_config(config_path)
        ref_data = pd.read_csv(config.reference_data)
        current_data = pd.read_csv(config.current_data)
        print(f"Loaded reference data: {len(ref_data)} rows")
        print(f"Loaded current data: {len(current_data)} rows")
        
        check = DriftCheck(ref_data)
        results = check.run(current_data, features=config.features)
        
        self._save_results(results, config.output)
        
        return results
    
    def _save_results(self, results, output_config):
        """Save results based on output configuration."""
        output_path = Path(output_config.path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        if output_config.format == "json":
           
            json_results = self._results_to_dict(results)
            with open(output_path, 'w') as f:
                json.dump(json_results, f, indent=2, default=str)
        
        print(f"Results saved to: {output_path}")
        
  
        if output_config.visualizations:
            print("Generating visualizations...")
    
            for feature, result in results.items():
                if hasattr(result, 'plot'):
                    result.plot()
    
    def _results_to_dict(self, results):
        """Convert your DriftCheck results to dictionary."""
     
        json_results = {}
        for feature, result in results.items():
            json_results[feature] = {
                'drift_detected': getattr(result, 'drift_detected', False),
                'psi_value': getattr(result, 'psi_value', None),
                'threshold': getattr(result, 'threshold', None),
               
            }
        return json_results