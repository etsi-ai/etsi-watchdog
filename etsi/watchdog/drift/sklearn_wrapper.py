# etsi/watchdog/drift/sklearn_wrapper.py

import numpy as np
import pandas as pd
from typing import Union, Optional, Dict, Any
from .base import DriftResult
from .factory import get_drift_function


class SklearnDriftDetector:
    """
    Scikit-learn compatible wrapper for drift detection functions.
    
    This class provides a unified interface for all drift detection methods
    in the package, making them compatible with Scikit-learn's API.
    """
    
    def __init__(self, method: str = 'psi', threshold: float = 0.2, **kwargs):
        """
        Initialize the drift detector.
        
        Parameters
        ----------
        method : str, default='psi'
            The drift detection method to use. Must be one of:
            - 'psi': Population Stability Index
            - 'ks': Kolmogorov-Smirnov test
            - 'shap': SHAP value based drift detection
        threshold : float, default=0.2
            Threshold for determining drift. Interpretation depends on the method.
        **kwargs : dict
            Additional keyword arguments passed to the underlying drift function.
        """
        self.method = method.lower()
        self.threshold = threshold
        self.drift_function = get_drift_function(self.method)
        self.kwargs = kwargs
        self.reference_data = None
        self.is_fitted = False
    
    def fit(self, X: Union[pd.DataFrame, np.ndarray], y: Optional[Union[pd.Series, np.ndarray]] = None) -> 'SklearnDriftDetector':
        """
        Fit the drift detector on reference data.
        
        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Reference data to detect drift from.
        y : array-like of shape (n_samples,), default=None
            Target values (ignored, for scikit-learn compatibility).
            
        Returns
        -------
        self : object
            Returns the instance itself.
        """
        if isinstance(X, np.ndarray):
            X = pd.DataFrame(X)
        
        self.reference_data = X
        self.is_fitted = True
        return self
    
    def transform(self, X: Union[pd.DataFrame, np.ndarray], **kwargs) -> Dict[str, DriftResult]:
        """
        Check for drift in the given data compared to the reference data.
        
        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            New data to check for drift.
            
        Returns
        -------
        dict
            Dictionary mapping feature names to DriftResult objects.
            
        Raises
        ------
        RuntimeError
            If the detector has not been fitted.
        """
        if not self.is_fitted:
            raise RuntimeError("This drift detector has not been fitted yet. Call 'fit' first.")
            
        if isinstance(X, np.ndarray):
            X = pd.DataFrame(X)
        
        results = {}
        for feature in X.columns:
            result = self.drift_function(
                reference_df=self.reference_data,
                current_df=X,
                feature=feature,
                threshold=self.threshold,
                **{**self.kwargs, **kwargs}
            )
            results[feature] = result
            
        return results
    
    def fit_transform(self, X: Union[pd.DataFrame, np.ndarray], y: Optional[Union[pd.Series, np.ndarray]] = None, **kwargs) -> Dict[str, DriftResult]:
        """
        Fit on reference data and detect drift in current data.
        
        This is a convenience method that combines fit() and transform().
        
        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Current data to check for drift.
        y : array-like of shape (n_samples,), default=None
            Target values (ignored, for scikit-learn compatibility).
            
        Returns
        -------
        dict
            Dictionary mapping feature names to DriftResult objects.
        """
        return self.fit(X, y).transform(X, **kwargs)
    
    def get_params(self, deep: bool = True) -> Dict[str, Any]:
        """
        Get parameters for this estimator.
        
        Parameters
        ----------
        deep : bool, default=True
            If True, will return the parameters for this estimator and
            contained subobjects that are estimators.
            
        Returns
        -------
        params : dict
            Parameter names mapped to their values.
        """
        return {
            'method': self.method,
            'threshold': self.threshold,
            **self.kwargs
        }
    
    def set_params(self, **params) -> 'SklearnDriftDetector':
        """
        Set the parameters of this estimator.
        
        Parameters
        ----------
        **params : dict
            Estimator parameters.
            
        Returns
        -------
        self : object
            Estimator instance.
        """
        for key, value in params.items():
            if key == 'method':
                self.method = value.lower()
                self.drift_function = get_drift_function(self.method)
            elif key == 'threshold':
                self.threshold = value
            else:
                self.kwargs[key] = value
        return self
