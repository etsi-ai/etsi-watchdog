
from typing import Dict, List, Optional, Union, Any
from dataclasses import dataclass

@dataclass
class AlgorithmConfig:
    name: str
    threshold: float
    params: Optional[Dict[str, Any]] = None

@dataclass
class OutputConfig:
    format: str 
    path: str
    visualizations: bool = True

@dataclass
class MonitoringConfig:
    window_size: int
    frequency: str 
    log_path: str

@dataclass
class DriftConfig:
    reference_data: str
    current_data: str
    features: List[str]
    algorithms: List[AlgorithmConfig]
    output: OutputConfig
    monitoring: Optional[MonitoringConfig] = None