import pandas as pd
from sklearn.tree import DecisionTreeClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score


class TreeDrift:
    def __init__(self, max_depth=5, test_size=0.3, random_state=42):
        self.max_depth = max_depth
        self.test_size = test_size
        self.random_state = random_state
        self.model = None
        self.auc_score = None
        self.feature_importance = None

    def fit(self, reference_df: pd.DataFrame, target_df: pd.DataFrame) -> float:
        """
        Train a decision tree classifier to distinguish reference and target data.
        Higher AUC = stronger drift. AUC ~0.5 = no drift.
        """
        ref = reference_df.copy()
        tgt = target_df.copy()

        ref['__label__'] = 0
        tgt['__label__'] = 1

        combined = pd.concat([ref, tgt], axis=0)
        X = combined.drop(columns=['__label__'])
        y = combined['__label__']

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=self.test_size, stratify=y, random_state=self.random_state
        )

        self.model = DecisionTreeClassifier(max_depth=self.max_depth, random_state=self.random_state)
        self.model.fit(X_train, y_train)

        y_pred = self.model.predict_proba(X_test)[:, 1]
        self.auc_score = roc_auc_score(y_test, y_pred)
        self.feature_importance = dict(zip(X.columns, self.model.feature_importances_))

        return self.auc_score

    def get_drift_report(self) -> dict:
        """
        Returns a summary of the drift detection result.
        """
        if self.auc_score is None:
            raise RuntimeError("fit() must be called before get_drift_report().")

        return {
            "drift_score_auc": round(self.auc_score, 4),
            "drift_detected": self.auc_score > 0.55,  # Threshold adjustable
            "feature_importance": self.feature_importance
        }
