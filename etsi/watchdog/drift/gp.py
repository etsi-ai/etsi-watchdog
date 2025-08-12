import numpy as np
import pandas as pd
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, DotProduct, ExpSineSquared, WhiteKernel
from sklearn.preprocessing import StandardScaler
from .base import DriftResult
import warnings

print("✅ gp_drift loaded from:", __file__)

def gp_drift(reference_df: pd.DataFrame, current_df: pd.DataFrame, feature: str, threshold: float = 0.2) -> DriftResult:
    """
    Gaussian Process-based drift detection for ordered sequences.
    Works with any numeric feature where sample order reflects progression (e.g., time, position).
    """
    if feature not in reference_df.columns or feature not in current_df.columns:
        warnings.warn(f"[etsi-watchdog] Feature '{feature}' not found in both datasets.")
        return None

    if not np.issubdtype(reference_df[feature].dtype, np.number):
        warnings.warn(f"[etsi-watchdog] Feature '{feature}' must be numeric for GP drift detection.")
        return None

    # Drop NaNs and preserve order
    reference_series = reference_df[feature].dropna().reset_index(drop=True)
    current_series = current_df[feature].dropna().reset_index(drop=True)

    if len(reference_series) < 10 or len(current_series) < 10:
        warnings.warn("[etsi-watchdog] Too few samples in reference or current data for GP drift detection.")
        return None

    # Construct synthetic feature: trend (index)
    X_train = np.arange(len(reference_series)).reshape(-1, 1)
    y_train = reference_series.values

    X_test = np.arange(len(reference_series), len(reference_series) + len(current_series)).reshape(-1, 1)
    y_test = current_series.values

    # Normalize features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Define flexible kernel
    kernel = RBF() + DotProduct() + ExpSineSquared() + WhiteKernel()

    gp = GaussianProcessRegressor(
        kernel=kernel,
        normalize_y=True,
        alpha=1e-6,
        n_restarts_optimizer=3
    )

    gp.fit(X_train_scaled, y_train)
    y_pred, y_std = gp.predict(X_test_scaled, return_std=True)

    # Detect drift where observed values fall outside the 98% confidence interval
    upper = y_pred + 2.33 * y_std
    lower = y_pred - 2.33 * y_std
    drift_flags = (y_test > upper) | (y_test < lower)

    drift_score = drift_flags.sum() / len(drift_flags)

    return DriftResult(
        method="gp",
        score=drift_score,
        threshold=threshold,
        sample_size=len(y_test),
        details={
            "drift_points": int(drift_flags.sum()),
            "drift_rate": float(drift_score),
            "confidence_level": "98%",
            "mean_pred_uncertainty": float(np.mean(y_std)),
            "max_pred_uncertainty": float(np.max(y_std)),
        }
    )
