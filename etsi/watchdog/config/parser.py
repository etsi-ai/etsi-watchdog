# etsi/watchdog/config/parser.py
import yaml
from typing import Dict, Any
from pathlib import Path
from .schema import DriftConfig, AlgorithmConfig, OutputConfig, MonitoringConfig

class ConfigParser:
    def load_config(self, config_path: str) -> DriftConfig:
        """Load YAML config and convert to DriftConfig object."""
     
        with open(config_path, 'r') as file:
            config_data = yaml.safe_load(file)
        
       
        drift_section = config_data['drift_detection']
        
      
        algorithms = []
        for algo_dict in drift_section['algorithms']:
            algorithms.append(AlgorithmConfig(**algo_dict))
        
        
        output_config = OutputConfig(**drift_section['output'])
        
       
        monitoring_config = None
        if 'monitoring' in drift_section:
            monitoring_config = MonitoringConfig(**drift_section['monitoring'])
        
        
        config = DriftConfig(
            reference_data=drift_section['reference_data'],
            current_data=drift_section['current_data'],
            features=drift_section['features'],
            algorithms=algorithms,
            output=output_config,
            monitoring=monitoring_config
        )
        
        return config