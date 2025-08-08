# tests/test_isolation_forest.py
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models.isolation_forest import IsolationForestModel
import pandas as pd
from sklearn.datasets import make_blobs

def test_isolation_forest_basic():
    X, _ = make_blobs(n_samples=100, centers=1, cluster_std=0.6, random_state=0)
    data = pd.DataFrame(X, columns=["x", "y"])

    model = IsolationForestModel()
    model.fit(data)
    predictions = model.predict(data)

    assert len(predictions) == len(data)
    assert set(predictions).issubset({-1, 1})
