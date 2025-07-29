# etsi/watchdog/drift/factory.py
from .tree_drift import TreeDrift
from .psi import psi_drift
from .shap_drift import shap_drift

def tree_drift(*args, **kwargs):
    return TreeDrift(*args, **kwargs)

def get_drift_function(algo: str):
    algo = algo.lower()
    if algo == "psi":
        return psi_drift
    elif algo == "shap":
        return shap_drift
    elif algo == "tree":
        return tree_drift  
    else:
        raise ValueError(f"Unsupported drift algorithm: {algo}")

