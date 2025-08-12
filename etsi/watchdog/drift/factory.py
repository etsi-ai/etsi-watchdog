# etsi/watchdog/drift/factory.py

from .psi import psi_drift
from .ks import ks_drift
from .shap_drift import shap_drift
from .wasserstein import wasserstein_drift
from .gp import gp_drift


def get_drift_function(algo: str):
    algo = algo.lower()
    if algo == "psi":
        return psi_drift
    elif algo == "ks":
        return ks_drift
    elif algo == "shap":
        return shap_drift
    elif algo == "wasserstein":
        return wasserstein_drift
    elif algo == "gp":
        return gp_drift
    else:
        raise ValueError(f"Unsupported drift algorithm: {algo}. Must be one of: 'psi', 'ks', 'shap', 'wasserstein', 'gp'")
