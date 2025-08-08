# models/isolation_forest.py

from sklearn.ensemble import IsolationForest
import pandas as pd

class IsolationForestModel:
    def __init__(self, n_estimators=100, contamination='auto', random_state=42):
        self.model = IsolationForest(
            n_estimators=n_estimators,
            contamination=contamination,
            random_state=random_state
        )
        self.is_fitted = False

    def fit(self, data: pd.DataFrame):
        self.model.fit(data)
        self.is_fitted = True

    def predict(self, data: pd.DataFrame):
        if not self.is_fitted:
            raise Exception("Model is not fitted yet.")
        return self.model.predict(data)  # -1 = anomaly, 1 = normal

    def anomaly_scores(self, data: pd.DataFrame):
        if not self.is_fitted:
            raise Exception("Model is not fitted yet.")
        return self.model.decision_function(data)
